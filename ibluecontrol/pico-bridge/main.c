/*
 * Pico -> ESP32 USB-UART bridge with esptool auto-reset.
 *
 * USB CDC <-> UART0 (GP0 = TX to ESP32 RXD0, GP1 = RX from ESP32 TXD0).
 * DTR/RTS from the host are translated exactly like the classic
 * two-transistor auto-program circuit used on ESP32 dev boards:
 *
 *   EN  (GP2) pulled low when RTS asserted and DTR not asserted
 *   IO0 (GP3) pulled low when DTR asserted and RTS not asserted
 *
 * EN/IO0 are only ever pulled LOW or released (high-Z). The Pico never
 * drives them high, so it cannot fight the board's own pull-ups/RC reset.
 */
#include "pico/stdlib.h"
#include "hardware/uart.h"
#include "hardware/irq.h"
#include "hardware/gpio.h"
#include "pico/bootrom.h"
#include <stdio.h>
#include "tusb.h"

#define BR_UART      uart0
#define BR_UART_IRQ  UART0_IRQ
#define PIN_TX       0
#define PIN_RX       1
#define PIN_EN       2
#define PIN_IO0      3
#define DEFAULT_BAUD 115200

#define RXBUF_SIZE 16384u /* power of two */
static uint8_t rxbuf[RXBUF_SIZE];
static volatile uint32_t rx_head, rx_tail;

static void od_set(uint pin, bool pull_low) {
    /* Open-drain emulation: output 0 when asserted, input (high-Z) when released. */
    gpio_set_dir(pin, pull_low ? GPIO_OUT : GPIO_IN);
}

static void od_init(uint pin) {
    gpio_init(pin);
    gpio_disable_pulls(pin);
    gpio_put(pin, 0);
    gpio_set_dir(pin, GPIO_IN);
}

/* Diagnostics, reported when the host opens the port at STATUS_BAUD */
static volatile uint32_t cnt_rx, cnt_oe, cnt_fe, cnt_be, cnt_pe, cnt_ringovf;
static volatile bool status_req, bootsel_req;
static absolute_time_t status_at;
#define STATUS_BAUD  1234
#define BOOTSEL_BAUD 1200

static void on_uart_rx(void) {
    while (uart_is_readable(BR_UART)) {
        uint32_t dr = uart_get_hw(BR_UART)->dr;
        if (dr & UART_UARTDR_OE_BITS) cnt_oe++;
        if (dr & UART_UARTDR_BE_BITS) cnt_be++;
        if (dr & UART_UARTDR_FE_BITS) cnt_fe++;
        if (dr & UART_UARTDR_PE_BITS) cnt_pe++;
        cnt_rx++;
        uint32_t next = (rx_head + 1) & (RXBUF_SIZE - 1);
        if (next != rx_tail) {
            rxbuf[rx_head] = (uint8_t)dr;
            rx_head = next;
        } else {
            cnt_ringovf++;
        }
    }
}

/*
 * Windows usbser.sys sends DTR and RTS as separate SET_CONTROL_LINE_STATE
 * requests, so esptool's "EN low -> (EN high + IO0 low)" step passes through
 * a brief (DTR=1,RTS=1) state that releases EN while IO0 is still high, and
 * the ESP32 boots normally. A real auto-reset circuit hides this with the RC
 * on EN; we emulate it by delaying every EN *release* by EN_RELEASE_DELAY_US.
 */
#define EN_RELEASE_DELAY_US 15000
static bool en_asserted;
static absolute_time_t en_release_at;
static bool en_release_pending;

/* Host toggled DTR/RTS */
void tud_cdc_line_state_cb(uint8_t itf, bool dtr, bool rts) {
    (void)itf;
    bool want_en_low = rts && !dtr;
    if (want_en_low) {
        en_release_pending = false;
        en_asserted = true;
        od_set(PIN_EN, true);
    } else if (en_asserted && !en_release_pending) {
        en_release_pending = true;
        en_release_at = make_timeout_time_us(EN_RELEASE_DELAY_US);
    }
    od_set(PIN_IO0, dtr && !rts);
#ifdef PICO_DEFAULT_LED_PIN
    gpio_put(PICO_DEFAULT_LED_PIN, dtr || rts);
#endif
}

/* Host changed baud/format */
void tud_cdc_line_coding_cb(uint8_t itf, cdc_line_coding_t const *lc) {
    (void)itf;
    if (lc->bit_rate == BOOTSEL_BAUD) {
        bootsel_req = true;
        return;
    }
    if (lc->bit_rate == STATUS_BAUD) {
        /* delay: Windows purges its RX buffer right after setting the baud */
        status_at = make_timeout_time_ms(300);
        status_req = true;
        return;
    }
    uart_tx_wait_blocking(BR_UART);
    uint data_bits = (lc->data_bits >= 5 && lc->data_bits <= 8) ? lc->data_bits : 8;
    uint stop_bits = (lc->stop_bits == 2) ? 2 : 1;
    uart_parity_t parity = (lc->parity == 1) ? UART_PARITY_ODD
                         : (lc->parity == 2) ? UART_PARITY_EVEN
                         : UART_PARITY_NONE;
    uint32_t baud = lc->bit_rate ? lc->bit_rate : DEFAULT_BAUD;
    uart_set_baudrate(BR_UART, baud);
    uart_set_format(BR_UART, data_bits, stop_bits, parity);
}

int main(void) {
    od_init(PIN_EN);
    od_init(PIN_IO0);

#ifdef PICO_DEFAULT_LED_PIN
    gpio_init(PICO_DEFAULT_LED_PIN);
    gpio_set_dir(PICO_DEFAULT_LED_PIN, GPIO_OUT);
    gpio_put(PICO_DEFAULT_LED_PIN, 0);
#endif

    uart_init(BR_UART, DEFAULT_BAUD);
    gpio_set_function(PIN_TX, GPIO_FUNC_UART);
    gpio_set_function(PIN_RX, GPIO_FUNC_UART);
    gpio_pull_up(PIN_RX); /* idle-high if the ESP32 side is unpowered */
    uart_set_hw_flow(BR_UART, false, false);
    uart_set_format(BR_UART, 8, 1, UART_PARITY_NONE);
    uart_set_fifo_enabled(BR_UART, true);
    irq_set_exclusive_handler(BR_UART_IRQ, on_uart_rx);
    irq_set_priority(BR_UART_IRQ, 0); /* highest: never let USB delay RX draining */
    irq_set_enabled(BR_UART_IRQ, true);
    uart_set_irq_enables(BR_UART, true, false);

    tusb_init();

    for (;;) {
        tud_task();

        if (en_release_pending && time_reached(en_release_at)) {
            en_release_pending = false;
            en_asserted = false;
            od_set(PIN_EN, false);
        }

        if (!tud_mounted())
            continue;

        if (bootsel_req) {
            sleep_ms(50); /* let the control transfer complete */
            reset_usb_boot(0, 0);
        }

        if (status_req && time_reached(status_at)) {
            status_req = false;
            char s[200];
            int n = snprintf(s, sizeof(s),
                "BRIDGE rx=%lu oe=%lu fe=%lu be=%lu pe=%lu ringovf=%lu\r\n",
                (unsigned long)cnt_rx, (unsigned long)cnt_oe, (unsigned long)cnt_fe,
                (unsigned long)cnt_be, (unsigned long)cnt_pe, (unsigned long)cnt_ringovf);
            tud_cdc_write(s, (uint32_t)n);
            tud_cdc_write_flush();
        }

        /* USB -> UART */
        while (tud_cdc_available()) {
            uint8_t buf[64];
            uint32_t n = tud_cdc_read(buf, sizeof(buf));
            if (n == 0)
                break;
            uart_write_blocking(BR_UART, buf, n);
        }

        /* UART -> USB */
        uint32_t head = rx_head;
        while (rx_tail != head) {
            uint32_t space = tud_cdc_write_available();
            if (space == 0)
                break;
            uint32_t chunk = (head > rx_tail) ? (head - rx_tail) : (RXBUF_SIZE - rx_tail);
            if (chunk > space)
                chunk = space;
            uint32_t written = tud_cdc_write(&rxbuf[rx_tail], chunk);
            rx_tail = (rx_tail + written) & (RXBUF_SIZE - 1);
            if (written < chunk)
                break;
        }
        tud_cdc_write_flush();
    }
}

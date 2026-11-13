cmake_minimum_required(VERSION 3.13)

include($ENV{PICO_SDK_PATH}/external/pico_sdk_import.cmake)

project(esp_bridge C CXX ASM)
set(CMAKE_C_STANDARD 11)

pico_sdk_init()

add_executable(esp_bridge main.c usb_descriptors.c)
target_include_directories(esp_bridge PRIVATE ${CMAKE_CURRENT_LIST_DIR})
target_link_libraries(esp_bridge PRIVATE
    pico_stdlib
    pico_unique_id
    pico_bootrom
    hardware_uart
    tinyusb_device
)

# UART0 belongs to the bridge; no stdio anywhere.
pico_enable_stdio_uart(esp_bridge 0)
pico_enable_stdio_usb(esp_bridge 0)

pico_add_extra_outputs(esp_bridge)

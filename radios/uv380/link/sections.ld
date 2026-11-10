/* UV-380 (TYT MD-UV380 / MD-UV390 / Retevis RT3S, STM32F405VGT6) link map, shared part;
 * radio.ld and qemu.ld set the memory regions and INCLUDE this.
 *
 * The flash origin is 0x0800C000 for the radio: TYT's bootloader owns the first 48 KB, and OpenRTX
 * keeps the last 128 KB for settings, so the app gets 1 MB - 48 KB - 128 KB. The QEMU build
 * (netduinoplus2, an STM32F405) links at 0x08000000 instead, where QEMU boots.
 *
 * TYT's bootloader checks the initial stack pointer with an off-by-one: it must lie inside SRAM,
 * so the stack top is 8 bytes below the end of SRAM, not at the end (openrtx.org,
 * hardware/mduv380.md).
 *
 * SRAM is 128 KB at 0x20000000 (DMA-capable); CCM is 64 KB at 0x10000000 (CPU only, no DMA): the
 * codec's state goes there (.ccm), DMA buffers stay in SRAM.
 *
 * SPDX-License-Identifier: MIT */
_estack = ORIGIN(sram) + LENGTH(sram) - 8;

ENTRY(reset_handler)

SECTIONS
{
    .text : ALIGN(4)
    {
        KEEP(*(.vectors))
        *(.text*)
        *(.rodata*)
        . = ALIGN(4);
    } > flash

    .ARM.exidx : { *(.ARM.exidx*) } > flash

    _sidata = LOADADDR(.data);
    .data : ALIGN(4)
    {
        _sdata = .;
        *(.data*)
        . = ALIGN(4);
        _edata = .;
    } > sram AT > flash

    .bss (NOLOAD) : ALIGN(4)
    {
        _sbss = .;
        *(.bss*)
        *(COMMON)
        . = ALIGN(4);
        _ebss = .;
    } > sram

    .ccm (NOLOAD) : ALIGN(4)
    {
        *(.ccm*)
    } > ccm

    _heap_start = _ebss;
    _heap_end = _estack - 8K;     /* 8 KB main stack */
    ASSERT(_ebss <= _heap_end, "SRAM full: static data runs into the 8 KB stack")
}

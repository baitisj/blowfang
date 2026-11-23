/* the UV-21 Pro dumper: page 0 of the app area (the bootloader starts the app at 0x08001000) */
MEMORY
{
  FLASH (rx) : ORIGIN = 0x08001000, LENGTH = 1K
  RAM (rwx)  : ORIGIN = 0x20000000, LENGTH = 16K
}
ENTRY(reset)
SECTIONS
{
  .text : { KEEP(*(.vectors)) *(.text*) *(.rodata*) } > FLASH
  .bss (NOLOAD) : { *(.bss*) *(COMMON) } > RAM   /* crc only: always set before it is read */
  /DISCARD/ : { *(.data*) *(.ARM.exidx*) *(.comment) }
}

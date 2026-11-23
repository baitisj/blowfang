/* the UV-21 Pro bring-up image: the app area (the bootloader starts the app at 0x08001000 and
   refuses anything from 0x08010000 - 0x1000 = offset 0xF000 up) */
MEMORY
{
  FLASH (rx) : ORIGIN = 0x08001000, LENGTH = 60K
  RAM (rwx)  : ORIGIN = 0x20000000, LENGTH = 16K
}
ENTRY(reset)
SECTIONS
{
  .text : { KEEP(*(.vectors)) *(.text*) *(.rodata*) . = ALIGN(4); } > FLASH
  .data : { _sdata = .; *(.data*) . = ALIGN(4); _edata = .; } > RAM AT > FLASH
  _sidata = LOADADDR(.data);
  .bss (NOLOAD) : { _sbss = .; *(.bss*) *(COMMON) . = ALIGN(4); _ebss = .; } > RAM
  /DISCARD/ : { *(.ARM.exidx*) *(.comment) }
}

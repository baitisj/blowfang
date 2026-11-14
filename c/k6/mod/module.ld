/* K6 RAM module: one position-independent block, code then constants. A module may put one
   table in section .k6tail: it goes last, so a tool can swap it for one of another size (midi.c).
   Its name block (K6_MODULE_NAME, section .k6name) goes first, where the module list reads it. MIT */
ENTRY(k6_main)
SECTIONS
{
  . = MODULE_BASE;
  .text : { KEEP(*(.k6name)) *(.text.k6_main) *(.text .text.*) *(.rodata .rodata.*) *(.k6tail) }
  .data : { *(.data .data.* .bss .bss.* COMMON) }
  /DISCARD/ : { *(.ARM.exidx* .ARM.extab* .comment .ARM.attributes) }
}
ASSERT(SIZEOF(.data) == 0, "a K6 module can't have writable statics (no RWPI): use the stack")

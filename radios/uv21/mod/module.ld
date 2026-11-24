/* UV-21 Pro RAM module: one position-independent block, code then constants (as the K6's and the
   UV-380's), entry um_main first. MIT */
ENTRY(um_main)
SECTIONS
{
  . = MODULE_BASE;
  .text : { *(.text.um_main) *(.text .text.*) *(.rodata .rodata.*) }
  .data : { *(.data .data.* .bss .bss.* COMMON) }
  /DISCARD/ : { *(.ARM.exidx* .ARM.extab* .comment .ARM.attributes) }
}
ASSERT(SIZEOF(.data) == 0, "a UV-21 module can't have writable statics (no RWPI): use the stack or the ram it is given")

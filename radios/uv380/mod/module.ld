/* UV-380 RAM module: one position-independent block, code then constants (as the K6's,
   c/k6/mod/module.ld), entry uv_main first. MIT */
ENTRY(uv_main)
SECTIONS
{
  . = MODULE_BASE;
  .text : { *(.text.uv_main) *(.text .text.*) *(.rodata .rodata.*) }
  .data : { *(.data .data.* .bss .bss.* COMMON) }
  /DISCARD/ : { *(.ARM.exidx* .ARM.extab* .comment .ARM.attributes) }
}
ASSERT(SIZEOF(.data) == 0, "a UV-380 module can't have writable statics (no RWPI): use the stack or api->scratch")

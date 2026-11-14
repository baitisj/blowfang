/* kp: EXPLORE's code pages in the 8 kHz voice + EXPLORE image (docs/k6-voice-explore.md).

   That image has no room in MCU flash for EXPLORE's sweep (xscan.c) and Listen for ID
   (xlisten.c, xdmr.c), nor for its repeater table code (rt.c) and station editor (sted.c).
   They are kept in SPI flash instead, as four pages in the SPI module
   library (k6_lib.h, header flag KM_F_PAGE), and run from RAM: the decoder pool of the shared
   block (hal::arena), which no voice message uses while EXPLORE runs. One page is in RAM at a
   time; the image loads the one a call needs (app::pager::explore, `page`).

   A page is linked for one image (tools/k6_pages.py): at the pool's address + KM_HDR, against
   that image's symbols, so its calls into the image (explore.c's ex_*, cwid.c's cw_*) are plain
   calls. The image calls into the page through the table at the page's start: the word
   KP_MAGIC(id, n), the table's own address (so a page linked for another place is refused),
   then n function addresses in the order below. The page's last 8 bytes are the
   image's build ID (its .note.gnu.build-id), which the image checks after each load, so a page
   made for another image is never run.

   The image (Rust, explore.rs) keeps the same lists in the same order.
   SPDX-License-Identifier: MIT */
#ifndef K6_KP_H
#define K6_KP_H

#define KP_SWEEP 1
#define KP_LISTEN 2
#define KP_TABLE 3
#define KP_EDIT 4
/* the library slots (k6 mod PAGE.bin --install SLOT --page) */
#define KP_SWEEP_SLOT 10
#define KP_LISTEN_SLOT 11
#define KP_TABLE_SLOT 9
#define KP_EDIT_SLOT 12
/* word 0 of a page: 'K' 'P', the page's id, its number of functions */
#define KP_MAGIC(id, n) (0x504Bu | (unsigned)(id) << 16 | (unsigned)(n) << 24)
/* the image's build ID at the page's end */
#define KP_ID_BYTES 8

/* the sweep page: xscan.c */
#define KP_SWEEP_FNS(X) \
  X(xs_defaults)        \
  X(xs_state_bytes)     \
  X(xs_mem_bytes)       \
  X(xs_init)            \
  X(xs_step)            \
  X(xs_close)           \
  X(xs_tone)            \
  X(xs_resume)          \
  X(xs_summary)         \
  X(xs_info)            \
  X(xs_up)              \
  X(xs_level)

/* the Listen for ID page: xlisten.c, xdmr.c */
#define KP_LISTEN_FNS(X) \
  X(xl_state_bytes)      \
  X(xl_start)            \
  X(xl_step)             \
  X(xl_stop)             \
  X(xl_info)             \
  X(xd_state_bytes)      \
  X(xd_start)            \
  X(xd_step)             \
  X(xd_stop)             \
  X(xd_known)            \
  X(xd_flags)            \
  X(xd_adj)              \
  X(xd_rate8)            \
  X(xd_tone)

/* the repeater table page: rt.c (its flash through the image's rt_flash_t calls) */
#define KP_TABLE_FNS(X) \
  X(rt_mount)           \
  X(rt_find)            \
  X(rt_next)            \
  X(rt_put)             \
  X(rt_delete)          \
  X(rt_cw_check)        \
  X(rt_cw_apply)        \
  X(rt_heard_apply)

/* the station editor page: sted.c (the release plan, Jeff 2026-11-13: the editor in the core;
   its state, a sted_t, is the image's, so only the code comes and goes) */
#define KP_EDIT_FNS(X) \
  X(sted_open)         \
  X(sted_key)          \
  X(sted_changed)      \
  X(sted_commit)       \
  X(sted_saved)        \
  X(sted_row)          \
  X(sted_hint)

#endif

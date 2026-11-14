/* kp_tab: a code page's table (kp.h), built once for each page: -DKP_PAGE=KP_SWEEP,
   KP_LISTEN, KP_TABLE or KP_EDIT; tools/k6_pages.py links it first in the page. SPDX-License-Identifier: MIT */
#include <stdint.h>
#include "kp.h"
#include "xscan.h"
#include "xlisten.h"
#include "xdmr.h"
#include "rt.h"
#include "sted.h"

#define KP_ADDR(f) (uintptr_t)f,
#define KP_ONE(f) +1

#if KP_PAGE == KP_SWEEP
#define KP_FNS KP_SWEEP_FNS
#elif KP_PAGE == KP_LISTEN
#define KP_FNS KP_LISTEN_FNS
#elif KP_PAGE == KP_TABLE
#define KP_FNS KP_TABLE_FNS
#elif KP_PAGE == KP_EDIT
#define KP_FNS KP_EDIT_FNS
#else
#error "build with -DKP_PAGE=KP_SWEEP, KP_LISTEN, KP_TABLE or KP_EDIT"
#endif

__attribute__((section(".kp_tab"), used))
const uintptr_t kp_tab[] = {KP_MAGIC(KP_PAGE, 0 KP_FNS(KP_ONE)), (uintptr_t)kp_tab, KP_FNS(KP_ADDR)};

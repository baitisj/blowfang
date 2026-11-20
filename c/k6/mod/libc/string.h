/* string.h for K6 RAM modules (modem/k6mod.py adds this folder with -isystem): the module has no
   C library, so a module that includes C written against one (sotto's encoder) defines these
   itself, as c/k6/mod/m0rt.h does. SPDX-License-Identifier: MIT */
#ifndef K6_MOD_STRING_H
#define K6_MOD_STRING_H
typedef unsigned int size_t;
void *memset(void *d, int c, size_t n);
void *memcpy(void *d, const void *s, size_t n);
void *memmove(void *d, const void *s, size_t n);
#endif

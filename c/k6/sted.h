/* sted: the K6 station editor's logic (the release plan, "The station editor"; Jeff 2026-11-13:
   in the core image, its logic in the table page). It edits one saved station, a repeater table
   record (rt.h), in RAM: the caller reads the record, hands it keys, draws the rows sted_row
   gives it, and writes the record (rt_put) only when sted_key says STED_SAVE. Nothing here
   touches the flash, the screen or the keypad, so the same code runs in the core, in a RAM
   module (mod/edtrial.c, the key trial) and on the host (tests/test_k6_sted.py).

   Two levels, so digits can be text. In the field list: UP/DOWN pick a field, # opens it (or
   flips WIDE and DMR), 5 saves, EXIT leaves (asks once if anything changed), MENU the same for
   the module list. Inside a field: digits type (multi-tap in NAME and CALL: 2 ABC .. 9 WXYZ,
   0 space 0, 1 1 / - .), UP/DOWN step a choice or the last letter, * deletes back (RX opens
   with its digits, so * takes them off one at a time and a digit typed first starts over; a
   tone's typed digits, then the tone), # closes the field, EXIT closes it and puts it back as
   it was. The offset: UP/DOWN step the common ones (simplex, +-600 kHz, +-1.6, +-5 MHz); *
   starts a custom one, typed in kHz (600, 5000, 7600), * deleting back and, with nothing
   typed, flipping its sign. 4 on RXT or TXT (in the list, or first in the open field) copies
   the other tone into it.

   Keys are Aura's KEY_TABLE letters as the modules read them: '0'-'9' '*' '#', 'U' up, 'D'
   down, 'M' menu, 'X' exit ('A' 'B' side keys, 'N' band, 'V' 'W' do nothing here).
   No division, no library calls, no writable statics. SPDX-License-Identifier: MIT */
#ifndef STED_H
#define STED_H
#include <stdint.h>
#include "rt.h"
#ifdef __cplusplus
extern "C" {
#endif

enum { SF_RX, SF_OFF, SF_RXT, SF_TXT, SF_NAME, SF_CALL, SF_GRP, SF_WIDE, SF_DMR, SF_N };

/* sted_key's results */
enum {
  STED_NONE = 0,     /* handled: redraw */
  STED_SAVE = 1,     /* write e->r (sted_commit first, then rt_put); then sted_saved */
  STED_EXIT = 2,     /* leave the editor (nothing written) */
  STED_MENU = 3      /* leave for the module list (nothing written) */
};

#define STED_TAP_MS 1000u     /* a second press of a key within this cycles its letters */
#define STED_LO_HZ 18000000u  /* the K6 receives 18-630 MHz (docs/k6-characteristics.md) */
#define STED_HI_HZ 630000000u
#define STED_ROW 22           /* a row: 21 characters and its NUL */

typedef struct {
  rt_rec_t r;          /* the record as edited */
  rt_rec_t was;        /* as opened or last saved: what "changed" is measured against */
  rt_rec_t fwas;       /* as the open field found it: EXIT inside a field puts it back */
  uint32_t tap_ms;     /* the last letter key's time */
  const char *msg;     /* a one-off line for the hint, until the next key */
  uint8_t f;           /* the field the cursor is on (SF_*) */
  uint8_t open;        /* 1 while that field is open */
  uint8_t ask;         /* 'X' or 'M' pressed once with changes: the second press leaves */
  uint8_t nd;          /* digits typed in RX, the offset (kHz) or a tone */
  uint8_t tap_k;       /* the last letter key, 0 none */
  uint8_t tap_i;       /* its letter */
  uint8_t fresh;       /* RX just opened with its digits: the first digit typed starts over */
  uint8_t neg;         /* the offset's sign while its kHz are typed */
  uint8_t cust;        /* * on the offset: its kHz are being typed */
  char dig[10];        /* the digits typed */
} sted_t;

/* Start on r (NULL: a new station, all blank, the cursor on RX) */
void sted_open(sted_t *e, const rt_rec_t *r);
/* One key at time ms: STED_* */
int sted_key(sted_t *e, int key, uint32_t ms);
/* 1 if e->r differs from what was opened or last saved (the fields the editor edits) */
int sted_changed(const sted_t *e);
/* Before rt_put: mark what the user typed (RT_F_NAME_USER, RT_F_CALL_USER, RT_F_TONE_USER; a
   typed call loses RT_C_FROM_CW and RT_C_TWICE) and make an auto-saved entry the user's (clears
   RT_F_AUTO), as Save does in EXPLORE */
void sted_commit(sted_t *e);
/* After the caller wrote e->r (its id, seq and crc as rt_put left them): nothing is changed now */
void sted_saved(sted_t *e, const rt_rec_t *written);
/* Field f's row into out (STED_ROW bytes): a 4-character label and the value, the open field
   with what is being typed and a '_' cursor. Returns the length. */
int sted_row(const sted_t *e, int f, char *out);
/* The hint line for the state (STED_ROW bytes): the keys that work now, or the one-off message.
   Returns the length. */
int sted_hint(const sted_t *e, char *out);
/* Frequency text: hz as MHz with dec decimals (1-6) into p; returns the end */
char *sted_mhz(char *p, uint32_t hz, int dec);
/* A tone (rt.h's form: CTCSS in 0.1 Hz, DCS | RT_TONE_DCS (| RT_TONE_DCS_INV), 0 none) as text:
   "103.5", "D023N", "D023I", "NONE"; returns the end */
char *sted_tone(char *p, uint16_t tone);

#ifdef __cplusplus
}
#endif
#endif

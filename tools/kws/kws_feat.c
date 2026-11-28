/* raw s16 8 kHz on stdin -> sotto 1200 -> per 20 ms frame: code wi ei c[19] as float32 on stdout */
#include <stdio.h>
#include <string.h>
#include "sotto.h"
#include "sotto_internal.h"
void sotto_lr_frames(const unsigned char *sf, const unsigned char *next, sotto_fparams_t *a, sotto_fparams_t *b);
static void emit(const sotto_fparams_t *p) {
  float v[3 + SOTTO_K - 1];
  v[0] = p->code; v[1] = p->wi; v[2] = p->ei;
  for (int i = 0; i < SOTTO_K - 1; i++) v[3 + i] = (float)p->c[i];
  fwrite(v, sizeof v, 1, stdout);
}
int main(void) {
  static unsigned char sf[100000][6];
  int n = 0;
  short x[SOTTO_LR_SAMPLES_PER_FRAME];
  struct sotto_lr_enc *e = sotto_lr_enc_create();
  size_t r;
  while (n < 99990 && (r = fread(x, 2, SOTTO_LR_SAMPLES_PER_FRAME, stdin)) > 0) {   /* room for the flush */
    if (r < SOTTO_LR_SAMPLES_PER_FRAME) memset(x + r, 0, 2 * (SOTTO_LR_SAMPLES_PER_FRAME - r));
    n += sotto_lr_encode(e, x, sf[n]);
  }
  while (n < 100000 && sotto_lr_enc_flush(e, sf[n])) n++;
  for (int s = 0; s < n; s++) {
    sotto_fparams_t a, b;
    sotto_lr_frames(sf[s], s + 1 < n ? sf[s + 1] : NULL, &a, &b);
    emit(&a); emit(&b);
  }
  return 0;
}

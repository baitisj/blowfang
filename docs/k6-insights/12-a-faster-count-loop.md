# 12. A faster count loop

- From: the study's CPU budget. `gp0dma`'s count loop takes 27%, the IF detector about 10-20%,
  and the sotto decoder 55-63%. A word-at-a-time masked-add loop (about 1 cycle a sample)
  would bring the counting to about 6%.
- Settled by: timing such a loop in a module (TIM17 counts cycles), as `gp0dma` already does.

# 23. The ARQ report's PHY is untested from the K6

- The chip's FSK has sent calibration bursts at 1200 baud; the reports go at the pager's
  F/1800. If the IC-7100 doesn't decode them, try the reports at 1200 (a REG_72 write
  around the send) before anything else.
- Settled by: the first `tools/k6_upload.py` run on the bench.

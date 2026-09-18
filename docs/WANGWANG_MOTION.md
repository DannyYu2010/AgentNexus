
## Transparent package correction v2

- The first transparent package was rejected by the user: an attached floor shadow appeared as a black tuft under the posterior belly, and the crown between the ears was not acceptably processed.
- A first procedural crown-fill attempt was rejected internally because it created a gray seam and was never installed.
- Final correction removes only the dark attached floor shadow in the lower-posterior region. The crown texture and alpha come from the approved 768px canonical master and follow each video frame through dense optical flow; the face is not regenerated.
- Corrected review: `work/wangwang/idle_approved/wangwang_idle_alpha_corrected_v3_review.mp4`.
- Focus QA: `work/wangwang/idle_approved/wangwang_idle_alpha_corrected_v3_focus_qa.jpg`.
- Corrected package: `work/wangwang/idle_approved/wangwang-idle-v2.pet`.
- Package SHA-256: `146e87e3ef614995d458d39539d7669ebb0e6518dd77b6d2480ceb33d8558832`.
- Installed as the active private `wangwang` pack. The rejected installed pack was retained as `wangwang.rejected-20260909-1`.

## Tail stability correction v3

- Transparent package v2 was rejected because the wagging tail flashed and changed shape.
- Frame-level analysis found the source tail itself stretches into inconsistent long/triangular shapes around frames 19, 22, 63, 86 and 89. Tail-region flow-aligned alpha edge MAE peaked at 0.523.
- A five-frame flow fusion reduced the mean edge residual by about 45% but retained the triangular source distortion, so it was rejected internally and not installed.
- Final correction extracts the clean frame-0 tail once and drives it around a fixed tail root with the measured, smoothed tail signal. Maximum rotation is 7.2 degrees, translation 2.2px horizontal and 1.2px vertical. Tail texture and silhouette no longer regenerate per frame.
- Review: `work/wangwang/idle_approved/wangwang_idle_alpha_corrected_v5_review.mp4`.
- Tail A/B: `work/wangwang/idle_approved/tail_analysis/v3_vs_v5_dark_ab.mp4`.
- Pack: `work/wangwang/idle_approved/wangwang-idle-v3.pet`, SHA-256 `8d09ba011648be8ac3f2792174ccf22ed43a4d4abd5ced3e27381f7138ea7178`.
- Installed as active private `wangwang`; previous v2 installation retained as `wangwang.rejected-20260909-2`.

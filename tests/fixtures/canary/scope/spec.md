# Scope: EMA denoising canary

## Method in plain language

Replace each sample by a weighted blend of the new sample (weight alpha) and the
previous smoothed value (weight 1 - alpha). The first smoothed value equals the
first sample. The baseline leaves the signal untouched.

## Scaled experiment

- Signal: one sine period, n = 500 samples.
- Noise: Gaussian, standard deviation 1.0, from `random.Random(seed)`.
- Seeds: 0, 1, 2 (public). Hidden tests use other seeds and other noise/alpha.
- No GPU, no dataset download. Pure Python.

## Claim

For every seed, `method_mse <= 0.5 * baseline_mse`. Tolerance: none; the margin
is already generous at these settings.

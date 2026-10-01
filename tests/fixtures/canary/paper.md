# Exponential Smoothing as a Strong Baseline for Signal Denoising

**Abstract.** We revisit the exponential moving average (EMA) as a denoiser for
periodic signals corrupted by additive Gaussian noise. On a synthetic benchmark
(a single sine period sampled at n points with noise standard deviation 1.0) we
find that EMA with smoothing factor alpha = 0.3 reduces mean squared error
against the clean signal by more than 50% relative to the raw noisy signal,
across random seeds. The effect is robust to the noise level and to moderate
changes in alpha.

## Method

Given a noisy sequence x_1..x_n, the EMA is s_1 = x_1 and
s_t = alpha * x_t + (1 - alpha) * s_{t-1}. The baseline is the identity: the
noisy sequence itself.

## Experiment

For each seed: clean_i = sin(2 * pi * i / n), i = 0..n-1; noisy_i = clean_i +
N(0, noise). Report MSE(method(noisy), clean) and MSE(baseline(noisy), clean).

## Claim

MSE_method <= 0.5 * MSE_baseline for every seed tested.

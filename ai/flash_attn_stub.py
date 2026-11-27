"""flash_attn の代替（Mac用・窓付きアテンションをPyTorchで実装）。

AIFS のチェックポイントは flash_attn.flash_attn_func を参照している。
CUDA 専用の flash_attn は Mac に入らないため、同じ引数・同じ窓の意味
（キー j は i-左 <= j <= i+右 の範囲のみ参照）を持つ関数を差し込む。
必ず anemoi を import する前に install() を呼ぶこと。
"""
import sys
import types

import torch
import torch.nn.functional as F

CHUNK = 2048  # 1回に処理するクエリ数（大きいほど速いがメモリ増）


def flash_attn_func(q, k, v, dropout_p=0.0, softmax_scale=None, causal=False, window_size=(-1, -1), **_):
    """q,k,v: (batch, grid, heads, dim) → 出力も同形"""
    assert not causal, "causal は未対応"
    left, right = window_size
    q, k, v = (t.transpose(1, 2) for t in (q, k, v))  # (B, H, N, D)
    n = q.shape[2]
    if left < 0 or right < 0:  # 窓なしは通常のアテンション
        return F.scaled_dot_product_attention(q, k, v, dropout_p=dropout_p, scale=softmax_scale).transpose(1, 2)
    outs = []
    for s in range(0, n, CHUNK):
        e = min(s + CHUNK, n)
        ks, ke = max(0, s - left), min(n, e + right)
        qi = torch.arange(s, e, device=q.device)[:, None]
        kj = torch.arange(ks, ke, device=q.device)[None, :]
        allowed = (kj >= qi - left) & (kj <= qi + right)
        # 真偽値マスクは内部で -inf になり、全面マスクのブロックでNaN/破損する（CPU・MPS）。
        # 有限の大きな負値の加算マスクにして回避する
        mask = torch.zeros(allowed.shape, dtype=q.dtype, device=q.device).masked_fill(~allowed, torch.finfo(q.dtype).min / 2)
        outs.append(F.scaled_dot_product_attention(
            q[:, :, s:e].contiguous(), k[:, :, ks:ke].contiguous(), v[:, :, ks:ke].contiguous(),
            attn_mask=mask, dropout_p=dropout_p, scale=softmax_scale,
        ))
    return torch.cat(outs, dim=2).transpose(1, 2).contiguous()


def install():
    mod = types.ModuleType("flash_attn")
    iface = types.ModuleType("flash_attn.flash_attn_interface")
    for m in (mod, iface):
        m.flash_attn_func = flash_attn_func
    mod.flash_attn_interface = iface
    sys.modules["flash_attn"] = mod
    sys.modules["flash_attn.flash_attn_interface"] = iface

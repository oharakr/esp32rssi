#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
csi_fading.py – Coleta dados CSI do ESP32 e gera histograma de fading.

Formato de entrada (linhas que começam com 'CSI,'):
  CSI,<ms>,<rodada>,<rssi_dBm>,<I0>,<Q0>,<I1>,<Q1>,...,<I63>,<Q63>

Uso – coleta pela serial:
  python csi_fading.py -p COM3
  python csi_fading.py -p COM3 -d 120           # para após 120 s
  python csi_fading.py -p COM3 -d 60 -s exp.txt # salva dados em arquivo

Uso – arquivo já salvo:
  python csi_fading.py -f exp.txt               # análise média de todas as subportadoras
  python csi_fading.py -f exp.txt -k 15         # subportadora 15: variação temporal,
                                                #   histograma + fits e fator-K
  python csi_fading.py -f exp.txt -A            # analisa todas as subportadoras de dados,
                                                #   identifica a mais Rayleigh e a mais Rice

Opções extras:
  -b 921600     baud rate alternativo
  --iqr 1.5     critério de outliers mais agressivo (padrão: 3.0)
  --no-filter   desativa remoção de outliers
"""

import argparse
import sys
import time
import numpy as np
import matplotlib.pyplot as plt
from scipy.stats import rayleigh, rice
try:
    import serial
except ImportError:
    serial = None  # só obrigatório para leitura serial


# ─────────────────────────────────────────────────────────────────────────────
# Mapeamento de subportadoras ESP32 HT20  (802.11n, FFT de 64 pontos)
#   Referência: IEEE 802.11n HT20; formato CSI ESP32 (IDF ≥ 4.x)
#   Índice 0..63 corresponde a cada par I+jQ retornado pelo firmware.
# ─────────────────────────────────────────────────────────────────────────────
_IDX_DC      = {0}                     # portadora DC
_IDX_GUARD   = set(range(27, 38))     # intervalo de guarda / portadoras nulas
_IDX_PILOTS  = {7, 21, 43, 57}       # subportadoras piloto (±7, ±21)
_IDX_ESP32_PROBLEM = {1}             # subportadora 1 com leitura problemática no ESP32
SUBCARRIERS_CONTROL = _IDX_DC | _IDX_GUARD | _IDX_PILOTS | _IDX_ESP32_PROBLEM
SUBCARRIERS_DATA    = [i for i in range(64) if i not in SUBCARRIERS_CONTROL]
# 51 subportadoras de dados: {2-6, 8-20, 22-26, 38-42, 44-56, 58-63}


# ─────────────────────────────────────────────────────────────────────────────
# Parsing
# ─────────────────────────────────────────────────────────────────────────────

def parse_line(line: str):
    """
    Retorna (ms, rodada, rssi, amplitudes_ndarray) ou None se inválido.
    amplitudes[k] = sqrt(I_k^2 + Q_k^2)
    """
    line = line.strip()
    if not line.startswith("CSI,"):
        return None
    parts = line.split(",")
    # precisa de pelo menos: CSI, ms, rodada, rssi, I0, Q0
    if len(parts) < 6:
        return None
    n_iq = len(parts) - 4
    if n_iq % 2 != 0:
        return None
    try:
        ms    = int(parts[1])
        rodada = int(parts[2])
        rssi  = int(parts[3])
        iq    = np.array(parts[4:], dtype=np.float32)
        I = iq[0::2]
        Q = iq[1::2]
        amp = np.sqrt(I ** 2 + Q ** 2)
        return ms, rodada, rssi, amp
    except (ValueError, IndexError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Fontes de dados
# ─────────────────────────────────────────────────────────────────────────────

def collect_serial(port: str, baudrate: int, duration: float) -> list:
    if serial is None:
        sys.exit("[ERRO] pyserial não instalado. Execute: pip install pyserial")
    lines = []
    ser = serial.Serial(port, baudrate=baudrate, timeout=1)
    msg = f"(máx {duration:.0f} s)" if duration > 0 else "(Ctrl+C para parar)"
    print(f"[INFO] {port} aberta. Coletando... {msg}")
    t0 = time.time()
    n_csi = 0
    try:
        while True:
            raw = ser.readline()
            if raw:
                decoded = raw.decode("utf-8", errors="replace").strip()
                lines.append(decoded)
                if decoded.startswith("CSI,"):
                    n_csi += 1
            elapsed = time.time() - t0
            if duration > 0:
                restante = max(0.0, duration - elapsed)
                print(f"\r[INFO] {n_csi} frames CSI | restam {restante:5.1f} s   ", end="", flush=True)
                if elapsed >= duration:
                    break
            else:
                print(f"\r[INFO] {n_csi} frames CSI | {elapsed:5.1f} s decorridos   ", end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        ser.close()
    print(f"\n[INFO] Coleta encerrada. {len(lines)} linhas, {n_csi} frames CSI.")
    return lines


def load_file(path: str) -> list:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return [l.rstrip("\n") for l in f]


# ─────────────────────────────────────────────────────────────────────────────
# Detecção de outliers por IQR
# ─────────────────────────────────────────────────────────────────────────────

def detecta_outliers(h: np.ndarray, fator: float = 3.0):
    """
    Usa o critério IQR para identificar outliers.
    Limite superior: Q3 + fator * IQR  (não há limite inferior negativo pois
    amplitudes >= 0; limite inferior: max(0, Q1 - fator*IQR)).

    fator=1.5 → critério clássico (agressivo para Rayleigh)
    fator=3.0 → apenas extremos (recomendado para fading)

    Retorna máscara booleana True = inlier.
    """
    Q1, Q3 = np.percentile(h, [25, 75])
    IQR = Q3 - Q1
    lo  = max(0.0, Q1 - fator * IQR)
    hi  = Q3 + fator * IQR
    mask = (h >= lo) & (h <= hi)
    return mask, lo, hi


# ─────────────────────────────────────────────────────────────────────────────
# K por subportadora
# ─────────────────────────────────────────────────────────────────────────────

def k_por_subportadora(frames: list, iqr_fator: float = 3.0):
    amp_matrix = np.array([f[3] for f in frames])  # shape (N_frames, N_sub)
    N_sub = amp_matrix.shape[1]
    K_dB_arr  = np.full(N_sub, np.nan)
    K_lin_arr = np.full(N_sub, np.nan)

    for k in range(N_sub):
        if k in SUBCARRIERS_CONTROL:
            continue  # não ajusta subportadoras de controle (DC, guarda, pilotos, idx 1)
        col = amp_matrix[:, k]
        mask, _, _ = detecta_outliers(col, fator=iqr_fator)
        col = col[mask]
        if len(col) < 10:
            continue
        col_n = col / col.mean()
        try:
            nu, _, s = rice.fit(col_n, floc=0)
            K = (nu / s) ** 2 / 2
            K_lin_arr[k] = K
            K_dB_arr[k]  = 10 * np.log10(K + 1e-9)
        except Exception:
            pass

    validos = ~np.isnan(K_dB_arr)
    print(f"[CSI/subcarr] {validos.sum()}/{N_sub} subportadoras ajustadas")
    print(f"[CSI/subcarr] K média={np.nanmean(K_dB_arr):.1f} dB  "
          f"mediana={np.nanmedian(K_dB_arr):.1f} dB  "
          f"min={np.nanmin(K_dB_arr):.1f} dB  max={np.nanmax(K_dB_arr):.1f} dB")
    return K_dB_arr, K_lin_arr, amp_matrix


def plot_por_subportadora(frames: list, iqr_fator: float = 3.0):
    K_dB, K_lin, amp_matrix = k_por_subportadora(frames, iqr_fator)
    N_sub    = amp_matrix.shape[1]
    sub_data = np.array([k for k in SUBCARRIERS_DATA if k < N_sub])

    fig, axes = plt.subplots(1, 3, figsize=(16, 4))
    fig.suptitle("Análise por subportadora CSI – ELT77G / UTFPR", fontsize=12)

    # (a) K(dB) – apenas subportadoras de dados (sem portadoras nulas nem de controle)
    K_data     = K_dB[sub_data]
    valid_mask = ~np.isnan(K_data)
    cores      = np.where(K_data[valid_mask] >= 0, "steelblue", "tomato")
    axes[0].bar(sub_data[valid_mask], K_data[valid_mask], color=cores, width=1.0)
    axes[0].axhline(0, color="black", lw=0.8, ls="--", label="K=0 dB (Rayleigh puro)")
    axes[0].axhline(np.nanmedian(K_data), color="orange", lw=1.5, ls="-",
                    label=f"Mediana {np.nanmedian(K_data):.1f} dB")
    axes[0].set_xlabel("Subportadora")
    axes[0].set_ylabel("K (dB)")
    axes[0].set_title("Fator-K por subportadora\n(azul=Rician, vermelho=Rayleigh)")
    axes[0].legend(fontsize=8)
    axes[0].grid(True, alpha=0.3, axis="y")

    # (b) histograma de K(dB) – apenas subportadoras de dados
    kv = K_data[~np.isnan(K_data)]
    axes[1].hist(kv, bins=20, color="steelblue", edgecolor="white", alpha=0.8)
    axes[1].axvline(0, color="black", lw=0.8, ls="--")
    axes[1].axvline(np.median(kv), color="orange", lw=1.5,
                    label=f"Mediana {np.median(kv):.1f} dB")
    axes[1].set_xlabel("K (dB)")
    axes[1].set_ylabel("Nº de subportadoras")
    axes[1].set_title("Distribuição de K entre subportadoras")
    axes[1].legend(fontsize=8)
    axes[1].grid(True, alpha=0.3)

    # (c) heatmap – apenas subportadoras de dados; escala por percentil para usar toda a paleta
    amp_data = amp_matrix[:, sub_data]
    vmin_hm  = float(np.percentile(amp_data, 2))
    vmax_hm  = float(np.percentile(amp_data, 98))
    im = axes[2].imshow(amp_data.T, aspect="auto", origin="lower",
                        cmap="inferno", vmin=vmin_hm, vmax=vmax_hm)
    axes[2].set_xlabel("Frame")
    axes[2].set_ylabel("Subportadora de dados (posição)")
    axes[2].set_title("Amplitude |H(t,k)| – heatmap\n(p2–p98, apenas subportadoras de dados)")
    fig.colorbar(im, ax=axes[2], label="|H|")

    plt.tight_layout()
    out2 = "fading_csi_subcarr.png"
    plt.savefig(out2, dpi=150)
    print(f"[INFO] Figura por subportadora salva em {out2}")
    plt.show()

    # ── histograma de 9 subportadoras aleatórias ─────────────────────────────
    # Restringe às subportadoras de dados (exclui controle, DC, idx 1, pilotos, guarda)
    validas = np.array([k for k in sub_data if not np.isnan(K_dB[k])])
    n_amostra = min(9, len(validas))
    rng = np.random.default_rng()
    escolhidas = sorted(rng.choice(validas, size=n_amostra, replace=False))
    print(f"[INFO] Subportadoras sorteadas: {escolhidas}")

    fig2, axes2 = plt.subplots(3, 3, figsize=(13, 10))
    fig2.suptitle("Histograma de fading – subportadoras sorteadas", fontsize=12)
    axes2_flat = axes2.flatten()

    for ax, k in zip(axes2_flat, escolhidas):
        col = amp_matrix[:, k]
        mask, _, _ = detecta_outliers(col, fator=iqr_fator)
        col = col[mask]
        col_n = col / col.mean()
        n_bins = max(15, int(np.ceil(np.log2(len(col_n)) + 1)))
        ax.hist(col_n, bins=n_bins, density=True,
                color="steelblue", edgecolor="white", alpha=0.75)
        x_lin = np.linspace(0, col_n.max() * 1.15, 300)

        # Rayleigh: tenta MLE; se falhar usa estimador de momentos (σ = √(E[H²]/2))
        try:
            _, s_ray = rayleigh.fit(col_n, floc=0)
        except Exception:
            s_ray = float(np.sqrt(np.mean(col_n ** 2) / 2))
        ax.plot(x_lin, rayleigh.pdf(x_lin, loc=0, scale=s_ray),
                "r-", lw=2, label=f"Rayleigh σ={s_ray:.3f}")

        # Rice: tenta MLE; se falhar não plota (precisa de variância suficiente)
        try:
            nu_r, _, s_r = rice.fit(col_n, floc=0)
            K_k = (nu_r / s_r) ** 2 / 2
            ax.plot(x_lin, rice.pdf(x_lin, nu_r, loc=0, scale=s_r),
                    "g--", lw=2, label=f"Rice K={10*np.log10(K_k+1e-9):.1f} dB")
        except Exception as e:
            print(f"[WARN] Rice fit falhou na subportadora {k}: {e}")
        ax.set_xlabel("|H|/E[|H|]")
        ax.set_ylabel("PDF")
        ax.set_title(f"Subportadora {k}  (n={len(col_n)})")
        ax.legend(fontsize=7)
        ax.grid(True, alpha=0.3)

    # esconde subplots sobrando se n_amostra < 9
    for ax in axes2_flat[n_amostra:]:
        ax.set_visible(False)

    plt.tight_layout()
    out3 = "fading_csi_hist9.png"
    plt.savefig(out3, dpi=150)
    print(f"[INFO] Histogramas individuais salvos em {out3}")
    plt.show()


# ─────────────────────────────────────────────────────────────────────────────
# Plot individual por subportadora  (variação temporal + histograma + K)
# ─────────────────────────────────────────────────────────────────────────────

def plot_subportadora_individual(frames: list, k: int,
                                 iqr_fator: float = 3.0,
                                 titulo_extra: str = ""):
    """
    Figura 1×2 para a subportadora k:
      col 0 – variação temporal de |H|
      col 1 – histograma + fit Rayleigh e Rice (K informado na legenda)
    """
    amp_matrix = np.array([f[3] for f in frames])
    ms_arr     = np.array([f[0] for f in frames], dtype=np.float64)
    t_s        = (ms_arr - ms_arr[0]) / 1000.0

    col = amp_matrix[:, k]
    mask, _, hi = detecta_outliers(col, fator=iqr_fator)
    n_out      = int((~mask).sum())
    col_clean  = col[mask]
    t_clean    = t_s[mask]

    if len(col_clean) < 10:
        print(f"[WARN] Subportadora {k}: dados insuficientes após filtragem "
              f"({len(col_clean)} amostras).")
        return

    col_n = col_clean / col_clean.mean()

    # Rayleigh fit
    try:
        _, s_ray = rayleigh.fit(col_n, floc=0)
    except Exception:
        s_ray = float(np.sqrt(np.mean(col_n ** 2) / 2))

    # Rice fit
    nu_r = s_r = K_lin = K_dB_val = None
    try:
        nu_r, _, s_r = rice.fit(col_n, floc=0)
        K_lin    = (nu_r / s_r) ** 2 / 2
        K_dB_val = 10 * np.log10(K_lin + 1e-9)
    except Exception as e:
        print(f"[WARN] Rice fit falhou na subportadora {k}: {e}")

    print(f"\n[Sub {k}] n={len(col_n)}  outliers={n_out}  "
          f"Rayleigh σ={s_ray:.4f}  " +
          (f"Rice K={K_dB_val:.1f} dB" if K_dB_val is not None else "Rice=falhou"))

    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    tag = f"Subportadora {k}" + (f"  –  {titulo_extra}" if titulo_extra else "")
    fig.suptitle(f"{tag}  –  ELT77G / UTFPR", fontsize=12)

    # ── col 0: variação temporal ──────────────────────────────────────────────
    axes[0].plot(t_clean, col_clean, lw=0.6, color="steelblue")
    if n_out > 0:
        axes[0].axhline(hi, color="red", ls=":", lw=1,
                        label=f"limite IQR×{iqr_fator}")
        axes[0].legend(fontsize=7)
    axes[0].set_xlabel("Tempo (s)")
    axes[0].set_ylabel("Amplitude |H|")
    axes[0].set_title(f"Variação temporal\n"
                      f"(n={len(col_clean)}, {n_out} outliers removidos)")
    axes[0].grid(True, alpha=0.3)

    # ── col 1: histograma + Rayleigh + Rice ───────────────────────────────────
    n_bins = max(20, int(np.ceil(np.log2(len(col_n)) + 1)))
    x_lin  = np.linspace(0, col_n.max() * 1.15, 300)
    axes[1].hist(col_n, bins=n_bins, density=True, color="steelblue",
                 edgecolor="white", alpha=0.75,
                 label=f"Dados (n={len(col_n)})")
    axes[1].plot(x_lin, rayleigh.pdf(x_lin, loc=0, scale=s_ray),
                 "r-", lw=2, label=f"Rayleigh σ={s_ray:.3f}")
    if nu_r is not None:
        k_str_leg = f"K={K_dB_val:.1f} dB"
        perfil    = "NLoS" if K_lin < 1 else "LoS"
        axes[1].plot(x_lin, rice.pdf(x_lin, nu_r, loc=0, scale=s_r),
                     "g--", lw=2, label=f"Rice {k_str_leg} ({perfil})")
    axes[1].set_xlabel("|H| / E[|H|]")
    axes[1].set_ylabel("PDF")
    axes[1].set_title("Histograma e ajuste de distribuição")
    axes[1].legend(fontsize=7)
    axes[1].grid(True, alpha=0.3)

    plt.tight_layout()
    out = f"fading_sub{k}.png"
    plt.savefig(out, dpi=150)
    print(f"[INFO] Figura salva em {out}")
    plt.show()


# ─────────────────────────────────────────────────────────────────────────────
# Análise de todas as subportadoras de dados
# ─────────────────────────────────────────────────────────────────────────────

def analise_todas_subportadoras(frames: list, iqr_fator: float = 3.0):
    """
    Analisa K por subportadora excluindo controle (DC, pilotos, guarda),
    identifica a mais próxima de Rayleigh (K mínimo) e de Rice (K máximo)
    e gera plots individuais para cada uma delas.
    """
    if not frames:
        sys.exit("[ERRO] Nenhum frame CSI válido para análise.")

    N_sub       = frames[0][3].size
    sub_validas = [k for k in SUBCARRIERS_DATA if k < N_sub]

    K_dB, K_lin, _ = k_por_subportadora(frames, iqr_fator)

    candidatas = [k for k in sub_validas if not np.isnan(K_lin[k])]
    if not candidatas:
        print("[WARN] Nenhuma subportadora de dados com ajuste válido.")
        return

    idx_rayleigh = min(candidatas, key=lambda k: K_lin[k])
    idx_rice     = max(candidatas, key=lambda k: K_lin[k])

    print("\n" + "=" * 62)
    print(f"  Subportadora mais próxima de RAYLEIGH: {idx_rayleigh:>3d}"
          f"  (K = {K_dB[idx_rayleigh]:+.1f} dB)")
    print(f"  Subportadora mais próxima de RICE    : {idx_rice:>3d}"
          f"  (K = {K_dB[idx_rice]:+.1f} dB)")
    print("=" * 62 + "\n")

    # Panorama geral (heatmap + K por subportadora)
    plot_por_subportadora(frames, iqr_fator=iqr_fator)

    # Plots individuais das subportadoras extremas
    print(f"[INFO] Plotando subportadora mais Rayleigh ({idx_rayleigh})...")
    plot_subportadora_individual(frames, idx_rayleigh, iqr_fator,
                                  titulo_extra="mais próxima de Rayleigh")
    print(f"[INFO] Plotando subportadora mais Rice ({idx_rice})...")
    plot_subportadora_individual(frames, idx_rice, iqr_fator,
                                  titulo_extra="mais próxima de Rice")


# ─────────────────────────────────────────────────────────────────────────────
# Análise + plots
# ─────────────────────────────────────────────────────────────────────────────
def analyse(lines: list, subcarrier, aplicar_filtro: bool = True, iqr_fator: float = 3.0):
    frames = [parse_line(l) for l in lines]
    frames = [f for f in frames if f is not None]

    if not frames:
        sys.exit("[ERRO] Nenhuma linha CSI válida encontrada.")

    ms_arr   = np.array([f[0] for f in frames], dtype=np.float64)
    rssi_arr = np.array([f[2] for f in frames], dtype=np.float64)

    # ── envelope por pacote ──────────────────────────────────────────────────
    if subcarrier is None:
        h = np.array([f[3].mean() for f in frames])
        titulo_h = "Amplitude média |H̄| (todas as subportadoras)"
    else:
        h = np.array([
            f[3][subcarrier] if subcarrier < len(f[3]) else np.nan
            for f in frames
        ])
        h = h[~np.isnan(h)]
        titulo_h = f"Amplitude da subportadora {subcarrier}"

    t_s = (ms_arr - ms_arr[0]) / 1000.0

    # ── diagnóstico de outliers ──────────────────────────────────────────────
    mask, lo, hi = detecta_outliers(h, fator=iqr_fator)
    n_out  = (~mask).sum()
    pct    = 100 * n_out / len(h)
    print(f"\n[OUTLIERS] critério: IQR × {iqr_fator}  →  [{lo:.3f}, {hi:.3f}]")
    print(f"[OUTLIERS] {n_out} amostras removidas de {len(h)}  ({pct:.1f}%)")
    if n_out > 0:
        print(f"[OUTLIERS] valores: {np.sort(h[~mask])}")

    h_clean = h[mask]
    t_clean = t_s[mask[:len(t_s)]]

    def _fit(arr):
        """Normaliza e retorna (h_norm, params_rayleigh, params_rice, K_lin, K_dB)."""
        hn = arr / arr.mean()
        _, s_ray = rayleigh.fit(hn, floc=0)
        nu_r, _, s_r = rice.fit(hn, floc=0)
        K = (nu_r / s_r) ** 2 / 2
        return hn, s_ray, nu_r, s_r, K, 10 * np.log10(K + 1e-9)

    hn_raw,   s_ray_raw,   nu_raw,   s_r_raw,   K_raw,   KdB_raw   = _fit(h)
    hn_clean, s_ray_clean, nu_clean, s_r_clean, K_clean, KdB_clean = _fit(h_clean)

    print(f"\n{'':30s} {'COM outliers':>15s}  {'SEM outliers':>15s}")
    print(f"{'N amostras':<30s} {len(h):>15d}  {len(h_clean):>15d}")
    print(f"{'Média amplitude':<30s} {h.mean():>15.3f}  {h_clean.mean():>15.3f}")
    print(f"{'Desvio-padrão':<30s} {h.std():>15.3f}  {h_clean.std():>15.3f}")
    print(f"{'Rayleigh σ':<30s} {s_ray_raw:>15.4f}  {s_ray_clean:>15.4f}")
    print(f"{'Rice K (linear)':<30s} {K_raw:>15.3f}  {K_clean:>15.3f}")
    print(f"{'Rice K (dB)':<30s} {KdB_raw:>15.1f}  {KdB_clean:>15.1f}")
    perfil = lambda K: "NLOS / Rayleigh" if K < 1 else f"LoS / Rician  K={K:.1f}"
    print(f"{'Perfil':<30s} {perfil(K_raw):>15s}  {perfil(K_clean):>15s}")
    print()

    h_final = h_clean if aplicar_filtro else h
    hn_final = hn_clean if aplicar_filtro else hn_raw
    label_final = "sem outliers" if aplicar_filtro else "todos os dados"

    # ── figura: 2 linhas × 3 colunas ────────────────────────────────────────
    fig, axes = plt.subplots(2, 3, figsize=(16, 8))
    fig.suptitle("Análise de Fading CSI – ELT77G / UTFPR", fontsize=12)

    def _plot_hist(ax, hn, s_ray, nu_r, s_r, K_dB, titulo_extra):
        x = np.linspace(0, hn.max() * 1.1, 400)
        # bins automáticos pela regra de Sturges ou mínimo de 20
        n_bins = max(20, int(np.ceil(np.log2(len(hn)) + 1)))
        ax.hist(hn, bins=n_bins, density=True, color="steelblue",
                edgecolor="white", alpha=0.7, label=f"Dados (n={len(hn)})")
        ax.plot(x, rayleigh.pdf(x, loc=0, scale=s_ray),
                "r-", lw=2, label=f"Rayleigh σ={s_ray:.3f}")
        ax.plot(x, rice.pdf(x, nu_r, loc=0, scale=s_r),
                "g--", lw=2, label=f"Rice K={K_dB:.1f} dB")
        ax.set_xlabel("|H|/E[|H|]")
        ax.set_ylabel("PDF")
        ax.set_title(f"Histograma {titulo_extra}")
        ax.legend(fontsize=7)
        ax.grid(True, alpha=0.3)

    # Linha 0: COM outliers
    axes[0, 0].plot(t_s, h, lw=0.5, color="steelblue", label="inliers")
    if n_out > 0:
        axes[0, 0].scatter(t_s[~mask[:len(t_s)]], h[~mask],
                           color="red", s=20, zorder=5, label=f"outliers ({n_out})")
        axes[0, 0].axhline(hi, color="red", ls=":", lw=1, label=f"limite IQR×{iqr_fator}")
    axes[0, 0].set_xlabel("Tempo (s)")
    axes[0, 0].set_ylabel("Amplitude")
    axes[0, 0].set_title(f"{titulo_h} – COM outliers")
    axes[0, 0].legend(fontsize=7)
    axes[0, 0].grid(True, alpha=0.3)

    _plot_hist(axes[0, 1], hn_raw, s_ray_raw, nu_raw, s_r_raw, KdB_raw, "COM outliers")

    axes[0, 2].plot(t_s, rssi_arr, lw=0.5, color="darkorange")
    axes[0, 2].axhline(rssi_arr.mean(), color="red", lw=1.5, ls="--",
                       label=f"Média: {rssi_arr.mean():.1f} dBm")
    axes[0, 2].set_xlabel("Tempo (s)")
    axes[0, 2].set_ylabel("RSSI (dBm)")
    axes[0, 2].set_title("RSSI ao longo do tempo")
    axes[0, 2].legend(fontsize=7)
    axes[0, 2].grid(True, alpha=0.3)

    # Linha 1: SEM outliers
    axes[1, 0].plot(t_clean, h_clean, lw=0.5, color="steelblue")
    axes[1, 0].set_xlabel("Tempo (s)")
    axes[1, 0].set_ylabel("Amplitude")
    axes[1, 0].set_title(f"{titulo_h} – SEM outliers")
    axes[1, 0].grid(True, alpha=0.3)

    _plot_hist(axes[1, 1], hn_clean, s_ray_clean, nu_clean, s_r_clean, KdB_clean, "SEM outliers")

    # Comparação de distribuições no mesmo eixo
    x2 = np.linspace(0, max(hn_raw.max(), hn_clean.max()) * 1.05, 400)
    axes[1, 2].hist(hn_raw, bins=40, density=True, color="steelblue",
                    alpha=0.4, label="Com outliers")
    axes[1, 2].hist(hn_clean, bins=40, density=True, color="darkorange",
                    alpha=0.4, label="Sem outliers")
    axes[1, 2].plot(x2, rice.pdf(x2, nu_raw,   loc=0, scale=s_r_raw),
                    "b-",  lw=2, label=f"Rice com  K={KdB_raw:.1f} dB")
    axes[1, 2].plot(x2, rice.pdf(x2, nu_clean, loc=0, scale=s_r_clean),
                    "r--", lw=2, label=f"Rice sem  K={KdB_clean:.1f} dB")
    axes[1, 2].set_xlabel("|H|/E[|H|]")
    axes[1, 2].set_ylabel("PDF")
    axes[1, 2].set_title("Comparação de ajuste")
    axes[1, 2].legend(fontsize=7)
    axes[1, 2].grid(True, alpha=0.3)

    plt.tight_layout()
    out = "fading_csi.png"
    plt.savefig(out, dpi=150)
    print(f"[INFO] Figura salva em {out}")
    plt.show()

    # ── sempre gera também a figura por subportadora ───────────────────────
    print("\n[INFO] Calculando K por subportadora (mais correto para fading)...")
    plot_por_subportadora(frames, iqr_fator=iqr_fator)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Análise de fading CSI do ESP32 – ELT77G",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""exemplos:
  # coletar 60 s e salvar
  python csi_fading.py -p COM3 -d 60 -s dados.txt

  # analisar subportadora 15 a partir de arquivo salvo
  python csi_fading.py -f dados.txt -k 15

  # identificar a subportadora mais Rayleigh e a mais Rice
  python csi_fading.py -f dados.txt -A
""")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("-p", "--port",
                     help="Porta serial (ex: COM3, /dev/ttyUSB0)")
    src.add_argument("-f", "--file",
                     help="Arquivo de texto com dados já capturados")
    parser.add_argument("-b", "--baud", type=int, default=115200,
                        help="Baud rate (padrão: 115200)")
    parser.add_argument("-d", "--duration", type=float, default=0,
                        help="Duração da coleta em segundos (0 = até Ctrl+C)")
    parser.add_argument("-s", "--save", metavar="ARQUIVO", default=None,
                        help="Salva os dados coletados via serial em ARQUIVO de texto")
    parser.add_argument("-k", "--subcarrier", type=int, default=None,
                        help="Analisar e plotar apenas a subportadora k "
                             "(variação temporal + histograma + fator-K)")
    parser.add_argument("-A", "--all-subcarriers", dest="all_subcarriers",
                        action="store_true",
                        help="Analisa todas as subportadoras de dados "
                             "(exclui DC, pilotos e guarda); indica qual é "
                             "mais Rayleigh e qual é mais Rice")
    parser.add_argument("--no-filter", dest="no_filter", action="store_true",
                        help="Não remove outliers antes do ajuste")
    parser.add_argument("--iqr", dest="iqr_fator", type=float, default=3.0,
                        help="Fator IQR para detecção de outliers (padrão: 3.0)")
    args = parser.parse_args()

    # ── coleta / leitura ──────────────────────────────────────────────────────
    if args.port:
        lines = collect_serial(args.port, args.baud, args.duration)
        if args.save:
            with open(args.save, "w", encoding="utf-8") as fout:
                fout.write("\n".join(lines))
            print(f"[INFO] Dados salvos em '{args.save}'")
    else:
        if args.save:
            parser.error("--save só é aplicável ao coletar pela serial (-p).")
        lines = load_file(args.file)

    # ── análise ───────────────────────────────────────────────────────────────
    if args.all_subcarriers:
        frames = [f for f in (parse_line(l) for l in lines) if f is not None]
        if not frames:
            sys.exit("[ERRO] Nenhuma linha CSI válida encontrada.")
        analise_todas_subportadoras(frames, iqr_fator=args.iqr_fator)

    elif args.subcarrier is not None:
        frames = [f for f in (parse_line(l) for l in lines) if f is not None]
        if not frames:
            sys.exit("[ERRO] Nenhuma linha CSI válida encontrada.")
        plot_subportadora_individual(frames, args.subcarrier,
                                     iqr_fator=args.iqr_fator)

    else:
        analyse(lines, None,
                aplicar_filtro=not args.no_filter,
                iqr_fator=args.iqr_fator)


if __name__ == "__main__":
    main()

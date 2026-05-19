# csi_fading.py — Coleta e Análise de Fading CSI (ESP32)

Ferramenta Python para coletar dados CSI (*Channel State Information*) do
ESP32 via porta serial e gerar análises de desvanecimento para a disciplina
**ELT77G – Noções de Telecomunicações / UTFPR-DAELT**.

---

## Pré-requisitos

```bash
pip install numpy scipy matplotlib pyserial
```

---

## Formato de entrada

Cada linha de dado CSI deve seguir o padrão gerado pelo firmware `rssi_ap.ino`:

```
CSI,<ms>,<rodada>,<rssi_dBm>,<I0>,<Q0>,<I1>,<Q1>,...,<I63>,<Q63>
```

| Campo | Descrição |
|---|---|
| `ms` | Timestamp em milissegundos desde o boot |
| `rodada` | Índice da rodada de medição |
| `rssi_dBm` | RSSI do pacote (dBm) |
| `I0..I63`, `Q0..Q63` | Parte real e imaginária das 64 subportadoras |

---

## Uso

### 1. Coletar pela serial (análise imediata)

```bash
python csi_fading.py -p COM3
python csi_fading.py -p COM3 -d 60          # para após 60 s
```

Gera análise da **amplitude média** de todas as subportadoras (figura 2×3) e
o panorama por subportadora.

---

### 2. Coletar e salvar em arquivo

```bash
python csi_fading.py -p COM3 -s dados.txt
python csi_fading.py -p COM3 -d 120 -s exp_los.txt
```

Após encerrar a coleta, os dados brutos são salvos em texto. A análise padrão
é executada em seguida.  
> A opção `-s` **só funciona com** `-p` (serial). Não é válida ao usar `-f`.

---

### 3. Analisar arquivo já salvo — visão geral

```bash
python csi_fading.py -f dados.txt
```

Executa a análise da amplitude média de todas as subportadoras e gera três arquivos:

| Arquivo | Conteúdo |
|---|---|
| `fading_csi.png` | Figura 2×3: variação temporal, histograma + fits Rayleigh/Rice, comparação com/sem outliers |
| `fading_csi_subcarr.png` | Panorama: fator-K por subportadora, histograma de K, heatmap \|H(t,k)\| |
| `fading_csi_hist9.png` | Histogramas de 9 subportadoras sorteadas com fits individuais |

---

### 4. Analisar uma subportadora específica  (`-k`)

```bash
python csi_fading.py -f dados.txt -k 15
python csi_fading.py -f dados.txt -k 42 --iqr 1.5
```

Gera uma figura focada `fading_sub15.png` com **três painéis**:

| Painel | Descrição |
|---|---|
| **Variação temporal** | Amplitude \|H\| ao longo do experimento; outliers marcados se presentes |
| **Histograma** | PDF empírica + curva Rayleigh ajustada (linha vermelha) + curva Rice ajustada (linha verde tracejada) |
| **Fator-K de Rice** | Barra horizontal com escala em dB; referências em 0 dB (Rayleigh puro), 3 dB e 10 dB (forte componente LoS); rótulo "LoS / Rician" ou "NLoS / Rayleigh" |

---

### 5. Análise completa de todas as subportadoras de dados  (`-A`)

```bash
python csi_fading.py -f dados.txt -A
python csi_fading.py -p COM3 -d 90 -s exp.txt -A
```

Exclui automaticamente as subportadoras de controle (DC, pilotos e guarda —
ver tabela abaixo) e:

1. Calcula o fator-K para cada subportadora de dados;
2. Imprime no terminal a subportadora **mais próxima de Rayleigh** (K mínimo) e
   a **mais próxima de Rice** (K máximo);
3. Gera o panorama (`fading_csi_subcarr.png`, `fading_csi_hist9.png`);
4. Gera `fading_subN.png` com a figura de 3 painéis para cada subportadora extrema.

Saída no terminal (exemplo):

```
==============================================================
  Subportadora mais próxima de RAYLEIGH:  22  (K = -4.3 dB)
  Subportadora mais próxima de RICE    :   5  (K = +8.7 dB)
==============================================================
```

---

## Referência de opções

| Opção | Padrão | Descrição |
|---|---|---|
| `-p PORT` | — | Porta serial (ex: `COM3`, `/dev/ttyUSB0`) |
| `-f FILE` | — | Arquivo de texto com dados já coletados |
| `-b BAUD` | `115200` | Baud rate da serial |
| `-d SEG` | `0` (∞) | Duração da coleta em segundos |
| `-s ARQUIVO` | — | Salva dados coletados (serial) em arquivo texto |
| `-k SUB` | — | Plota variação temporal + histograma + K da subportadora `SUB` |
| `-A` / `--all-subcarriers` | — | Analisa todas as subportadoras de dados; identifica a mais Rayleigh e a mais Rice |
| `--no-filter` | — | Não remove outliers antes do ajuste (diagnóstico ainda exibido) |
| `--iqr FATOR` | `3.0` | Fator IQR para detecção de outliers (`1.5` = mais agressivo) |

---

## Subportadoras de controle excluídas pela opção `-A`

Para 802.11n HT20 (FFT de 64 pontos, formato CSI ESP32):

| Tipo | Índices no vetor CSI |
|---|---|
| DC (nulo) | 0 |
| Guarda / nulos | 27 – 37 |
| Pilotos | 7, 21, 43, 57 |
| **Dados (analisadas)** | **1–6, 8–20, 22–26, 38–42, 44–56, 58–63** |

Total de subportadoras de dados: **52**.

---

## Saídas geradas

| Arquivo | Gerado quando |
|---|---|
| `fading_csi.png` | sem `-k` e sem `-A` |
| `fading_csi_subcarr.png` | sem `-k` e sem `-A`; ou com `-A` |
| `fading_csi_hist9.png` | sem `-k` e sem `-A`; ou com `-A` |
| `fading_subN.png` | com `-k N`; ou com `-A` (para as 2 subportadoras extremas) |

---

## Exemplos de fluxo completo

```bash
# 1. Coletar 2 min (LoS), salvar e analisar subportadora 12
python csi_fading.py -p COM3 -d 120 -s los.txt
python csi_fading.py -f los.txt -k 12

# 2. Coletar 2 min (NLoS), salvar e identificar subportadoras extremas
python csi_fading.py -p COM3 -d 120 -s nlos.txt
python csi_fading.py -f nlos.txt -A

# 3. Coletar, salvar e já identificar subportadoras extremas de uma vez
python csi_fading.py -p COM3 -d 90 -s exp.txt -A

# 4. Usar critério de outlier mais agressivo
python csi_fading.py -f exp.txt -A --iqr 1.5
```

---

## Interpretação do fator-K

| K (dB) | Perfil de canal |
|---|---|
| K < 0 dB | Predominantemente espalhamento (Rayleigh / NLoS) |
| 0 – 5 dB | Transição Rayleigh → Rice |
| 5 – 15 dB | Canal Rice com componente LoS moderado |
| > 15 dB | Canal quasi-determinístico (forte LoS ou reflexão dominante) |

A distribuição de Rayleigh corresponde ao limite K → −∞ dB (nenhuma componente dominante).

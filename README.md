# Medição de RSSI e CSI com ESP32

Repositório de apoio à disciplina ELT77G / Noções de Telecomunicações da UTFPR,
voltado a experimentos de medição de canal sem fio com ESP32 em modo Access
Point. O projeto combina:

- firmware para o ESP32, responsável por criar o AP Wi-Fi, receber requisições
  HTTP, medir RSSI e, opcionalmente, exportar CSI pela serial;
- scripts de análise em Python e MATLAB para estudar perda de percurso e
  desvanecimento em cenários LoS e NLoS.

## Objetivo

O repositório foi organizado para permitir que o estudante:

- monte um experimento simples de canal sem fio usando um ESP32 e um celular;
- colete amostras de RSSI em diferentes distâncias e cenários;
- ajuste um modelo de perda de percurso do tipo log-distância;
- investigue desvanecimento Rayleigh/Rice a partir de RSSI e CSI;
- compare o comportamento do canal em diferentes subportadoras do OFDM.

## Arquivos principais

### Firmware ESP32

- `rssi_ap.ino`
  Firmware principal do experimento. O ESP32 cria um Access Point Wi-Fi,
  hospeda uma interface web de medição em `http://192.168.4.1`, registra RSSI
  por pacote e pode transmitir CSI pela serial quando `HABILITAR_CSI = 1`.

### Análise de CSI

- `csi_fading.py`
  Script principal para captura e análise de CSI. Permite:
  - coletar dados pela serial;
  - salvar os dados brutos em arquivo texto;
  - analisar uma subportadora específica;
  - analisar todas as subportadoras válidas e identificar a mais próxima de
    Rayleigh e a mais próxima de Rice.
- `csi_fading_README.md`
  Documentação detalhada do uso de `csi_fading.py`, com exemplos de comando,
  formato dos dados e descrição das saídas gráficas.

## Requisitos

### Hardware

- 1 módulo ESP32 compatível com Arduino
- 1 celular ou notebook com Wi-Fi para gerar tráfego ao AP
- cabo USB para gravação e captura serial

### Software

- Arduino IDE 2.x com suporte a ESP32 by Espressif
- Python 3.10+ recomendado

Dependências Python usadas nos scripts:

```bash
pip install numpy scipy matplotlib pandas pyserial
```

## Fluxo típico de uso

### 1. Gravar o firmware no ESP32

Abra `rssi_ap.ino` na Arduino IDE e ajuste ao menos:

- `AP_SSID`
- `AP_PASSWORD`
- `AP_CHANNEL`
- `HABILITAR_CSI`

Depois grave o sketch no ESP32.

### 2. Coletar RSSI via interface web

Após o boot, o ESP32 sobe um AP Wi-Fi. Conecte o celular ao AP e abra:

```text
http://192.168.4.1
```

Use a interface para iniciar as medições, repetir rodadas em diferentes
distâncias e exportar os resultados em CSV.

Esses scripts cobrem:

- inspeção dos dados coletados;
- cálculo de RSSI média por rodada;
- ajuste do modelo log-distância;
- análise estatística básica do desvanecimento.

### 3. Coletar e analisar CSI

Com `HABILITAR_CSI = 1`, o ESP32 passa a emitir linhas `CSI,...` pela serial.
O script `csi_fading.py` pode ser usado para captura e análise.

Exemplos:

```bash
python csi_fading.py -p COM3 -d 120 -s csi_los.txt
python csi_fading.py -f csi_los.txt -k 12
python csi_fading.py -f csi_los.txt -A
```

Para detalhes completos, consulte `csi_fading_README.md`.

## Formatos de dados

### CSV de RSSI

Gerado pela interface web do ESP32 com informações de distância, comentário,
índice da rodada e RSSI em dBm.

### Texto de CSI

As linhas válidas seguem o padrão:

```text
CSI,<ms>,<rodada>,<rssi_dBm>,<I0>,<Q0>,...,<I63>,<Q63>
```

O script de CSI interpreta esses pares I/Q, calcula a amplitude por
subportadora e ajusta distribuições de Rayleigh e Rice.

## Saídas esperadas

Dependendo do modo de execução, os scripts podem gerar figuras como:

- `fading_csi.png`
- `fading_csi_subcarr.png`
- `fading_csi_hist9.png`
- `fading_subN.png`

Essas figuras ajudam a visualizar:

- evolução temporal da amplitude do canal;
- histograma do envelope e ajuste de distribuições;
- fator K por subportadora;
- comportamento diferencial entre subportadoras do OFDM.

## Observações

- A subportadora `1` é excluída da análise CSI por apresentar leitura
  problemática no ESP32 utilizado no experimento.
- As subportadoras de controle, pilotos, DC e guarda também são excluídas das
  análises por subportadora.
- O roteiro da prática foi atualizado para incluir uma seção específica sobre
  CSI, fading por subportadora e uma introdução breve a OFDM.
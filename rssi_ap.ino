/*
 * ============================================================
 *  RSSI AP  –  ELT77G Noções de Telecomunicações  –  UTFPR / DAELT
 * ============================================================
 *
 * O ESP32 cria um Access Point WiFi e serve uma página web
 * com um botão "Iniciar Medição". Ao pressionar o botão, o
 * celular do aluno envia NUM_PACKETS requisições HTTP para o
 * AP. A cada requisição o AP registra a RSSI daquele cliente
 * (dBm). Os dados podem ser exportados em CSV diretamente do
 * browser para posterior análise no MATLAB.
 *
 * Fluxo:
 *   1. Grave o sketch no ESP32
 *   2. Conecte o celular ao AP (SSID + senha configurados abaixo)
 *   3. Abra o browser em  http://192.168.4.1
 *   4. Posicione-se na distância desejada e clique em "Iniciar"
 *   5. Repita o passo 4 para cada distância
 *   6. Clique em "Baixar CSV" e use o script MATLAB para análise
 *
 * Hardware: qualquer módulo ESP32 (DevKit, WROOM-32, etc.)
 * IDE:      Arduino IDE 2.x  +  suporte ESP32 by Espressif 2.x/3.x
 * ============================================================
 */

#include <WiFi.h>
#include <WebServer.h>
#include "esp_wifi.h"

// ============================================================
//  >>>  CONFIGURAÇÃO DO ALUNO – edite apenas esta seção  <<<
// ============================================================

// Coloque aqui o nome da sua equipe ou o seu RA.
// Este nome aparecerá como o SSID do Access Point WiFi.
// Use apenas letras, números e underscores (sem espaços nem acentos).
const char* AP_SSID = "ELT77G_EquipeXX";

// Senha do Access Point (mínimo 8 caracteres)
const char* AP_PASSWORD = "elt77g123";

// Canal WiFi do Access Point (1 a 13).
// Cada equipe deve usar um canal diferente para evitar interferência mútua.
// Sugestão: equipe 1 → canal 1, equipe 2 → canal 6, equipe 3 → canal 11,
//           equipes extras → 2, 3, 4, 5, 7, 8, 9, 10, 12, 13
const int AP_CHANNEL = 1;

// Número de pacotes enviados a cada medição
const int NUM_PACKETS = 100;

// CSI (Channel State Information): 1 = transmite I/Q de cada subportadora
//   pela Serial durante as medições. Capture com um script Python no PC.
//   0 = desabilitado (economiza processamento)
#define HABILITAR_CSI  1

// ============================================================
//  Não é necessário alterar nada abaixo desta linha
// ============================================================

#define MAX_AMOSTRAS  10000  // Limite de amostras na RAM (~60 KB estáticos)
#define MAX_RODADAS      50  // Máximo de medições distintas (dist + comentário)
#define MAX_CMT_LEN     48  // Comprimento máximo do comentário

// ------------------------------------------------------------
//  CSI – ring buffer SPSC (callback WiFi → loop principal)
// ------------------------------------------------------------
#if HABILITAR_CSI
#define CSI_RING_SZ   16   // slots simultâneos no ring
#define CSI_MAX_LLTF  64   // subportadoras LLTF (HT20 / 20 MHz)

struct CsiFrame {
  uint32_t ms;
  int8_t   rssi;
  uint8_t  rodada;
  uint8_t  npares;                  // pares I/Q válidos neste frame
  int8_t   iq[CSI_MAX_LLTF * 2];   // I0,Q0,I1,Q1,...
};
static CsiFrame     csiRing[CSI_RING_SZ];
static volatile int csiHead      = 0;
static volatile int csiTail      = 0;
static volatile uint8_t csiRodada = 0;
static volatile bool    csiAtivo = false;
#endif  // HABILITAR_CSI

WebServer servidor(80);

// Um "rodada" representa uma rodada de medição com distância e comentário fixos
struct Rodada {
  float dist_m;
  char  cmt[MAX_CMT_LEN];
};

struct Amostra {
  int8_t  rssi;
  uint8_t rodada;  // índice em rodadas[]
};

static Rodada   rodadas[MAX_RODADAS];
static int     totalRodadas    = 0;
static Amostra amostras[MAX_AMOSTRAS];
static int      totalAmostras = 0;
static uint32_t totalDrops   = 0;  // pings recebidos mas descartados (buffer cheio)

// ------------------------------------------------------------
//  CSI callback + drena ring (compilado apenas se HABILITAR_CSI)
// ------------------------------------------------------------
#if HABILITAR_CSI
// Roda na task WiFi – deve ser rápido e sem bloqueio.
static void csiCallback(void *ctx, wifi_csi_info_t *info) {
  if (!csiAtivo || !info || info->len < 2) return;
  int next = (csiHead + 1) % CSI_RING_SZ;
  if (next == csiTail) return;             // ring cheio: descarta
  CsiFrame &f  = csiRing[csiHead];
  f.ms         = millis();
  f.rssi       = info->rx_ctrl.rssi;
  f.rodada      = csiRodada;
  f.npares     = (uint8_t)min(info->len / 2, CSI_MAX_LLTF);
  memcpy(f.iq, info->buf, f.npares * 2);
  csiHead      = next;
}

// Imprime um frame por chamada (não bloqueia).
// Formato: CSI,<ms>,<rodada>,<rssi>,<I0>,<Q0>,<I1>,<Q1>,...
static void drenaCsiRing() {
  if (csiTail == csiHead) return;
  CsiFrame &f = csiRing[csiTail];
  Serial.printf("CSI,%lu,%u,%d", f.ms, (unsigned)f.rodada, (int)f.rssi);
  for (int k = 0; k < f.npares; k++)
    Serial.printf(",%d,%d", (int)f.iq[2 * k], (int)f.iq[2 * k + 1]);
  Serial.print('\n');
  csiTail = (csiTail + 1) % CSI_RING_SZ;
}
#endif  // HABILITAR_CSI

// ------------------------------------------------------------
// Retorna a RSSI do cliente conectado ao AP.
// Como cada ESP32 pertence a um aluno e recebe apenas um
// celular por vez, basta ler o primeiro (e único) slot da
// lista de estações associadas.
// ------------------------------------------------------------
static int8_t getRSSICliente() {
  wifi_sta_list_t wifiList;
  memset(&wifiList, 0, sizeof(wifiList));
  if (esp_wifi_ap_get_sta_list(&wifiList) != ESP_OK || wifiList.num == 0) {
    return 0;
  }
  return wifiList.sta[0].rssi;
}

// ------------------------------------------------------------
// Página HTML – armazenada em flash (PROGMEM)
// Os marcadores __SSID__, __N__ e __TOTAL__ são substituídos
// em tempo de execução pelo handler handleRoot().
// ------------------------------------------------------------
static const char INDEX_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html lang="pt-br">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>RSSI – ELT77G</title>
  <style>
    *    { box-sizing: border-box; }
    body { font-family: sans-serif; max-width: 480px; margin: 2rem auto;
           padding: 0 1.2rem; color: #222; }
    h1   { font-size: 1.4rem; color: #003366; margin-bottom: .2rem; }
    .sub { font-size: .85rem; color: #666; margin-bottom: 1.5rem; }
    .card{ background: #f5f7fa; border-radius: 8px; padding: 1rem;
           margin-bottom: 1rem; }
    label{ font-size: .9rem; color: #555; }
    strong { color: #003366; }
    button {
      padding: .75rem 1.8rem; font-size: 1rem;
      background: #003366; color: #fff;
      border: none; border-radius: 6px; cursor: pointer;
      margin-right: .5rem; margin-top: .5rem;
    }
    button:disabled { background: #999; cursor: default; }
    button.danger   { background: #c0392b; }
    button.success  { background: #27ae60; }
    #progresso { margin-top: .8rem; font-size: .95rem; min-height: 1.2rem; }
    #barraFundo{ height: 10px; background: #ddd; border-radius: 5px;
                 margin-top: .4rem; }
    #barra     { height: 100%; width: 0; background: #003366;
                 border-radius: 5px; transition: width .08s; }
    a.dl-btn   { display: inline-block; padding: .75rem 1.8rem;
                 background: #27ae60; color: #fff; border-radius: 6px;
                 text-decoration: none; font-size: 1rem; margin-top: .5rem; }
    #log       { font-size: .78rem; color: #555; margin-top: .6rem;
                 max-height: 120px; overflow-y: auto; white-space: pre; }
  </style>
</head>
<body>
  <h1>Medição de RSSI</h1>
  <p class="sub">ELT77G – Noções de Telecomunicações | UTFPR</p>

  <div class="card">
    <label>AP:</label> <strong>__SSID__</strong><br>
    <label>Amostras armazenadas:</label>
    <strong id="total">__TOTAL__</strong>
  </div>

  <div class="card">
    <label for="npack"><strong>Número de pacotes por medição:</strong></label><br>
    <input id="npack" type="number" min="1" max="2000" step="1" value="__N__"
           style="width:100%;padding:.5rem;font-size:1rem;margin-top:.3rem;
                  border:1px solid #bbb;border-radius:4px;">

    <label for="dist" style="margin-top:.8rem;display:block;">
      <strong>Distância até o ESP32 (m):</strong>
    </label>
    <input id="dist" type="number" min="0" step="0.1" placeholder="ex: 2.5"
           style="width:100%;padding:.5rem;font-size:1rem;margin-top:.3rem;
                  border:1px solid #bbb;border-radius:4px;">

    <label for="cmt" style="margin-top:.8rem;display:block;">
      <strong>Comentário (opcional):</strong>
    </label>
    <input id="cmt" type="text" maxlength="47" placeholder="ex: LoS, com obstáculo..."
           style="width:100%;padding:.5rem;font-size:1rem;margin-top:.3rem;
                  border:1px solid #bbb;border-radius:4px;">
  </div>

  <button id="btn" onclick="medir()">&#9654; Iniciar Medição</button>
  <a class="dl-btn" href="/results.csv">&#8659; Baixar CSV</a>
  <button class="danger" onclick="zerar()">&#10005; Zerar amostras</button>

  <div id="progresso">Aguardando...</div>
  <div id="barraFundo"><div id="barra"></div></div>
  <div id="log"></div>

  <script>
    async function medir() {
      const btn   = document.getElementById('btn');
      const prog  = document.getElementById('progresso');
      const barra = document.getElementById('barra');
      const log   = document.getElementById('log');
      const N     = parseInt(document.getElementById('npack').value);
      const dist  = parseFloat(document.getElementById('dist').value);
      const cmt   = document.getElementById('cmt').value.trim();

      if (isNaN(N) || N < 1) {
        alert('Informe um número de pacotes válido (mínimo 1).');
        return;
      }

      if (isNaN(dist) || dist < 0) {
        alert('Informe uma distância válida (em metros) antes de iniciar.');
        return;
      }

      btn.disabled = true;
      log.textContent = '';
      let erros = 0;

      // Registra o rodada (distância + comentário) no ESP e obtém o índice
      let rodada = 0;
      try {
        const r = await fetch(`/start?dist=${dist}&cmt=${encodeURIComponent(cmt)}`,
                              { cache: 'no-store' });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        rodada = parseInt(await r.text());
      } catch(e) {
        prog.textContent = 'Erro ao registrar medição: ' + e.message;
        btn.disabled = false;
        return;
      }

      for (let i = 0; i < N; i++) {
        try {
          const resp = await fetch(`/ping?g=${rodada}`, { cache: 'no-store' });
          if (!resp.ok) throw new Error('HTTP ' + resp.status);
        } catch(e) {
          erros++;
          log.textContent += `[${i+1}] erro: ${e.message}\n`;
          log.scrollTop = log.scrollHeight;
        }
        const pct = ((i + 1) / N * 100).toFixed(0);
        barra.style.width = pct + '%';
        prog.textContent  = `Progresso: ${i + 1} / ${N}  (${pct}%)`;
      }

      prog.textContent = `✓ Concluído! ${N - erros} pacotes OK, ${erros} erros.`;
      document.getElementById('total').textContent =
        await (await fetch('/count', { cache: 'no-store' })).text();
      btn.disabled = false;
    }

    async function zerar() {
      if (!confirm('Apagar TODAS as amostras armazenadas?')) return;
      await fetch('/clear');
      document.getElementById('total').textContent = '0';
      document.getElementById('progresso').textContent = 'Amostras zeradas.';
      document.getElementById('barra').style.width = '0';
    }
  </script>
</body>
</html>
)rawliteral";

// ------------------------------------------------------------
// Handlers
// ------------------------------------------------------------

void handleRoot() {
  String html = FPSTR(INDEX_HTML);
  html.replace("__SSID__",  AP_SSID);
  html.replace("__N__",     String(NUM_PACKETS));
  html.replace("__TOTAL__", String(totalAmostras));
  servidor.send(200, "text/html; charset=utf-8", html);
}

void handleStart() {
  servidor.sendHeader("Access-Control-Allow-Origin", "*");
  servidor.sendHeader("Cache-Control", "no-store");

  if (totalRodadas >= MAX_RODADAS) {
    servidor.send(503, "text/plain", "max rodadas atingido");
    return;
  }

  float dist = 0.0f;
  if (servidor.hasArg("dist")) {
    dist = servidor.arg("dist").toFloat();
  }
  String cmt = "";
  if (servidor.hasArg("cmt")) {
    cmt = servidor.arg("cmt");
    if (cmt.length() >= MAX_CMT_LEN) cmt = cmt.substring(0, MAX_CMT_LEN - 1);
  }

  rodadas[totalRodadas].dist_m = dist;
  strncpy(rodadas[totalRodadas].cmt, cmt.c_str(), MAX_CMT_LEN - 1);
  rodadas[totalRodadas].cmt[MAX_CMT_LEN - 1] = '\0';

  Serial.printf("[RODADA %d] dist=%.2f m  cmt=%s\n", totalRodadas, dist, rodadas[totalRodadas].cmt);

#if HABILITAR_CSI
  csiRodada = (uint8_t)totalRodadas;
  csiAtivo = true;
#endif

  servidor.send(200, "text/plain", String(totalRodadas++));
}

void handlePing() {
  servidor.sendHeader("Access-Control-Allow-Origin", "*");
  servidor.sendHeader("Cache-Control", "no-store");

  if (totalAmostras < MAX_AMOSTRAS) {
    uint8_t g = 0;
    if (servidor.hasArg("g")) {
      int gv = servidor.arg("g").toInt();
      if (gv >= 0 && gv < totalRodadas) g = (uint8_t)gv;
    }

    int8_t rssi = getRSSICliente();

    amostras[totalAmostras].rssi  = rssi;
    amostras[totalAmostras].rodada = g;
    totalAmostras++;

    Serial.printf("[%4d] rodada=%d  RSSI: %4d dBm\n", totalAmostras, g, rssi);
  } else {
    totalDrops++;
    Serial.printf("[DROP #%lu] buffer cheio (%d/%d amostras)\n",
                  totalDrops, totalAmostras, MAX_AMOSTRAS);
  }
  servidor.send(200, "text/plain", "ok");
}

void handleCount() {
  servidor.sendHeader("Access-Control-Allow-Origin", "*");
  servidor.send(200, "text/plain", String(totalAmostras));
}

void handleResultsCSV() {
  // Envia o CSV em streaming (chunks) para evitar alocação de um bloco grande
  // na heap fragmentada do ESP32.
  servidor.sendHeader("Content-Disposition",
                      "attachment; filename=\"rssi_data.csv\"");
  servidor.sendHeader("Access-Control-Allow-Origin", "*");
  servidor.sendHeader("Cache-Control", "no-store");
  servidor.setContentLength(CONTENT_LENGTH_UNKNOWN);
  servidor.send(200, "text/csv; charset=utf-8", "");

  servidor.sendContent("n,rodada,dist_m,comentario,rssi_dBm\n");

  // Envia em blocos de 32 linhas para não lotar o buffer de envio
  const int CHUNK = 32;
  String buf;
  buf.reserve(CHUNK * 50);

  for (int i = 0; i < totalAmostras; i++) {
    uint8_t g = amostras[i].rodada;
    buf += String(i + 1)                + ","
         + String(g)                    + ","
         + String(rodadas[g].dist_m, 2)  + ","
         + String(rodadas[g].cmt)        + ","
         + String(amostras[i].rssi)     + "\n";

    if ((i + 1) % CHUNK == 0 || i == totalAmostras - 1) {
      servidor.sendContent(buf);
      buf = "";
    }
  }

  Serial.printf("[CSV] %d linhas enviadas, %lu drops registrados.\n",
                totalAmostras, totalDrops);
}

void handleClear() {
  totalAmostras = 0;
  totalRodadas   = 0;
  totalDrops    = 0;
#if HABILITAR_CSI
  csiAtivo = false;
#endif
  servidor.sendHeader("Access-Control-Allow-Origin", "*");
  servidor.send(200, "text/plain", "ok");
  Serial.println("[CLEAR] Amostras, rodadas e drops apagados.");
}

void handleNotFound() {
  servidor.send(404, "text/plain", "Not found");
}

// ------------------------------------------------------------
// Setup
// ------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\n==============================");
  Serial.println("  RSSI AP – ELT77G / UTFPR");
  Serial.println("==============================");
  Serial.printf("SSID         : %s\n", AP_SSID);
  Serial.printf("Canal        : %d\n", AP_CHANNEL);
  Serial.printf("Pacotes/med. : %d\n", NUM_PACKETS);
  Serial.printf("Max amostras : %d\n", MAX_AMOSTRAS);

  WiFi.mode(WIFI_AP);
  WiFi.softAP(AP_SSID, AP_PASSWORD, AP_CHANNEL);

  Serial.print("AP IP        : ");
  Serial.println(WiFi.softAPIP());

#if HABILITAR_CSI
  esp_wifi_set_csi_rx_cb(csiCallback, NULL);
  esp_wifi_set_csi(true);
  Serial.println("CSI          : habilitado");
  Serial.println("  Capture a Serial e filtre linhas que comecam com 'CSI'");
  Serial.println("  Formato: CSI,<ms>,<rodada>,<rssi>,<I0>,<Q0>,...,<I63>,<Q63>");
#endif
  Serial.println("------------------------------");

  servidor.on("/",            HTTP_GET, handleRoot);
  servidor.on("/start",       HTTP_GET, handleStart);
  servidor.on("/ping",        HTTP_GET, handlePing);
  servidor.on("/count",       HTTP_GET, handleCount);
  servidor.on("/results.csv", HTTP_GET, handleResultsCSV);
  servidor.on("/clear",       HTTP_GET, handleClear);
  servidor.onNotFound(handleNotFound);

  servidor.begin();
  Serial.println("Servidor HTTP iniciado.");
  Serial.println("Acesse: http://" + WiFi.softAPIP().toString());
  Serial.println("==============================\n");
}

// ------------------------------------------------------------
// Loop
// ------------------------------------------------------------
void loop() {
  servidor.handleClient();
#if HABILITAR_CSI
  drenaCsiRing();
#endif
}

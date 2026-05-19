% ===================================================================
% ELT77G -- Noções de Telecomunicações -- UTFPR / DAELT
% Arquivo de análise: Perda de Percurso e Desvanecimento
%
% Preencha todas as seções marcadas com TODO.
% NÃO altere as linhas sem marcação TODO.
%
% Dependências: MATLAB R2019b+ com Symbolic Math Toolbox
% ===================================================================
clear; close all; clc;

%% ================================================================
%% SEÇÃO 1 -- Importação e inspeção dos dados  (Parte 2, item 1)
%% ================================================================

% TODO 1: Carregue o arquivo rssi_data.csv com readtable.
dados = readtable("rssi_data.csv");
dados = sortrows(dados, 'dist_m');

tipo = 'NLoS'

% Extraia as colunas (NÃO modifique estas linhas)
dist_m   = dados.dist_m;
rssi_dBm = dados.rssi_dBm;
rodadas   = dados.rodada;

% TODO 2: Faça plot de rssi_dBm (eixo y) vs dist_m (eixo x).
%         Use rodadas como variável de cor. Adicione rótulos e grade.
figure;
???
grid on;

%% ================================================================
%% SEÇÃO 2 -- RSSI média por rodada  (Parte 2, item 2)
%% ================================================================

g_ids    = unique(rodadas);
n_rodadas = length(g_ids);
rssi_med  = zeros(1, n_rodadas);
dist_med  = zeros(1, n_rodadas);
cmt_rodada = cell(1, n_rodadas);

for i = 1:n_rodadas
    idx = rodadas == g_ids(i);

    % TODO 3: Calcule rssi_med(i) como a média de rssi_dBm para o rodada i.
    rssi_med(i) = ???

    % TODO 4: Calcule dist_med(i) como a média de dist_m para o rodada i.
    dist_med(i) = ???

    % Salva o comentário do rodada (NÃO modifique)
    cmt_rodada{i} = upper(dados.comentario{find(idx, 1)});
end

%% ================================================================
%% SEÇÃO 3 -- Ajuste do modelo log-distância  (Parte 2, item 3)
%% ================================================================
% Consulte o Apêndice do roteiro para um exemplo numérico completo.
% Os valores do seu experimento serão diferentes -- resolva a partir do zero.

[M idx] = min(dist_med);
rssi_d0      = rssi_med(idx);   % RSSI em d0
d0           = dist_med(idx);       % distância de referência (1 m)

syms n_sym

% TODO 6: Construa MSE como a soma dos erros quadráticos entre cada
%         rssi_med(i) e o modelo log-distância em dist(i).
%         Equação de referência (roteiro):
%           J(n) = sum_i ( RSSI_i - rssi_d0 + 10*n*log10(dist_i / d0) )^2

syms n;
MSE = 0;
for i = 1:length(dist_med)
    MSE_i   = ???
    MSE = MSE + vpa(MSE_i);
end

% TODO 7: Derive MSE_los em relação a n_sym, iguale a zero e resolva.
dMSE_dn = ???
n = ???
n = double(n);
fprintf('n (%s) = %.3f\n', tipo, n);

%% ================================================================
%% SEÇÃO 4 -- Gráfico medido vs. modelo  (Parte 2, item 4)
%% ================================================================

d_plot = linspace(d0, max(dist_med), 200);

% TODO 9: Calcule Pr_modelo usando n e o modelo log-distância e plote os dados medidos e a curva com o n do modelo.
Pr_modelo = ???

figure
plot(???);
hold on
plot(???);
hold off

xlabel('Distância (m)');  ylabel('RSSI médio (dBm)');
legend('Location', 'best', 'FontSize', 14);
title('Perda de Percurso - Modelo Log-Distância');
grid on;
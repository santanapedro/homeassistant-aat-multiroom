# AAT Multiroom — integração para Home Assistant

Integração custom para controlar amplificadores **AAT Multiroom Digital**
(PMR, PMRH, PMA) pela rede, usando o protocolo TCP documentado em
"AAT Digital Matrix Amplifiers - API (TCP/SERIAL/IR) Rev.12".

## Escopo desta v1

Só controle das **zonas do amplificador** — sem streamer embutido (PMR-9 a
PMR-13), sem tons (grave/agudo), sem balanço, sem ganho de pré-amp, sem
modo festa/grupos. Por zona, você tem:

- **Power** da zona (liga/desliga o amplificador daquela zona — comando
  `ZSTDBYON`/`ZSTDBYOFF`, não o power geral do aparelho)
- **Volume** (0–87, como no aparelho)
- **Mute**
- **Seleção de entrada por botões** (um botão por entrada, dentro do
  dispositivo daquela zona) — em vez de uma lista suspensa

## Instalação

### Via HACS (recomendado)

1. No HACS, abra o menu (⋮) → **Repositórios personalizados**.
2. Adicione a URL deste repositório com o tipo **Integration**.
3. Procure "AAT Multiroom" no HACS e instale.
4. Reinicie o Home Assistant.

### Manual

1. Copie a pasta `custom_components/aat_multiroom` para dentro de
   `<pasta de configuração do Home Assistant>/custom_components/`.
   Resultado esperado: `config/custom_components/aat_multiroom/manifest.json`.
2. Reinicie o Home Assistant.

### Depois de instalar (HACS ou manual)

1. Vá em **Configurações → Dispositivos e serviços → Adicionar integração**
   e procure por "AAT Multiroom".
2. Informe o **IP** do amplificador na rede (porta TCP padrão é `5000`;
   normalmente não precisa alterar).
3. A integração vai se conectar, perguntar o modelo (`MODEL`) e o estado
   completo (`GETALL`) para descobrir quantas zonas existem, e então mostra
   uma tela para você nomear o equipamento, cada zona e cada entrada de
   áudio (com sugestões já pré-preenchidas, tipo "Zona 1", "Entrada 1").
4. Pronto — cada zona vira um dispositivo separado dentro do Multiroom, com
   um `media_player` (power/volume/mute) e um botão por entrada.

Para renomear zonas/entradas depois, use o botão **Configurar** na
integração (não precisa remover e adicionar de novo).

## Múltiplos multirooms

Repita o processo de adicionar integração para cada amplificador AAT que
você tiver. Cada um é uma conexão TCP e um estado completamente separados —
um multiroom cair, travar ou ser reconfigurado não afeta os outros.

## Como funciona por baixo dos panos (fluidez)

- A integração mantém uma **conexão TCP persistente** com cada amplificador
  (não abre/fecha conexão a cada comando).
- Ao clicar em algo (ligar zona, mexer volume, mutar, trocar entrada), o
  estado na tela é atualizado **imediatamente** (otimista), e o comando é
  enviado em paralelo; a resposta real do aparelho confirma/corrige o valor
  quando chega (tipicamente poucos milissegundos na rede local).
- O protocolo AAT também envia mensagens não solicitadas quando o estado
  muda por outra via (controle remoto IR, painel frontal, outro app) — a
  integração escuta isso continuamente, então essas mudanças também
  aparecem quase instantaneamente no Home Assistant, sem precisar esperar
  um ciclo de atualização.
- Existe também uma sincronização periódica de segurança (a cada 30s) e
  reconexão automática com backoff, caso a conexão caia.

## Limitações conhecidas / próximos passos possíveis

- Não implementa tons (bass/treble), balanço, ganho de pré-amp, modo
  festa/agrupamento de zonas nem o streamer embutido dos modelos PMR-9 a
  PMR-13 — pode ser adicionado depois como entidades `number`/`switch`
  adicionais, sem precisar redesenhar o que já existe.
- A contagem de entradas por modelo (usada só para sugerir quantos botões
  criar no primeiro setup) vem de uma tabela estática baseada na capa do
  manual; se o seu modelo específico tiver menos entradas ligadas
  fisicamente do que o padrão do modelo, os botões extras simplesmente não
  farão efeito (o próprio aparelho ignora comandos para entradas
  inexistentes).

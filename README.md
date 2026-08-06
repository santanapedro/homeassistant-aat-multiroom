# AAT Multiroom — integração para Home Assistant

Integração custom para controlar amplificadores **AAT Multiroom Digital**
(PMR, PMRH, PMA) pela rede, usando o protocolo TCP documentado em
"AAT Digital Matrix Amplifiers - API (TCP/SERIAL/IR) Rev.12".

Esta é uma integração não-oficial, sem afiliação com a AAT (Advanced Audio
Technologies) — por isso o ícone (`custom_components/aat_multiroom/brand/`)
usa a marca da **Avant Tecnologia**, mantenedora deste projeto, e não a
logo da AAT. HA 2026.3+ permite que integrações custom tragam seu próprio
ícone dessa forma, sem depender do repositório central
`home-assistant/brands`. Os arquivos (`icon.png`, `logo.png` e
`dark_logo.png`, com variantes `@2x`) são gerados a partir da arte
original com `tools/make_icon.py <arquivo-fonte>`, que remove o fundo
branco e recorta/redimensiona conforme as especificações de imagem do HA.

## Escopo desta versão

Só controle das **zonas do amplificador** — sem streamer embutido (PMR-9 a
PMR-13), sem tons (grave/agudo), sem balanço, sem ganho de pré-amp, sem
modo festa/grupos.

Por multiroom (dispositivo "hub"):

- **Switch de power geral** (`PWRON`/`PWROFF`) — liga/desliga o aparelho
  inteiro de uma vez.

Por zona (cada zona é um dispositivo próprio dentro do multiroom):

- **`media_player`** com power, volume (0–87, como no aparelho) e mute
  embutidos (o toggle de power aqui usa `ZSTDBYON`/`ZSTDBYOFF`, que é o
  stand-by daquela zona específica, não o power geral do aparelho). A
  entrada atual aparece como informação secundária no card (via
  `media_title`, o campo que a maioria dos cards usa como subtítulo — não
  existe "faixa tocando" de verdade numa zona de amplificador, então
  reaproveitamos esse campo pra mostrar a entrada), e também dá pra trocar
  de entrada direto pelo seletor de fonte do próprio card (`source`/
  `source_list`), além dos switches por entrada abaixo.
- **Switch de power da zona** — o mesmo `ZSTDBYON`/`ZSTDBYOFF`, só que como
  uma entidade separada e sempre visível, com ícone que muda conforme o
  estado (`mdi:speaker` ligado / `mdi:speaker-off` desligado), útil em
  layouts de dashboard onde o toggle dentro do card do media_player não é
  tão visível.
- **Switch por entrada** (`INPSET`) — um switch por entrada de áudio, em
  vez de uma lista suspensa. Ligar um deles seleciona aquela entrada; o
  switch da entrada ativa fica com o ícone "pintado" (`mdi:radiobox-marked`,
  na cor de destaque do tema) e os demais aparecem apagados
  (`mdi:radiobox-blank`) — são mutuamente exclusivos, como um grupo de
  rádio. Desligar o switch da entrada já ativa não faz nada (o aparelho não
  tem um estado de "nenhuma entrada", só dá pra trocar pra outra).
- **Sensor "Entrada atual"** — mostra em texto simples o nome da entrada
  selecionada naquela zona, sempre visível em qualquer dashboard sem
  precisar configurar nada.

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
   um `media_player` (power/volume/mute), um switch por entrada de áudio
   (com destaque visual na entrada ativa) e um sensor mostrando a entrada
   atual em texto.

Para renomear zonas/entradas depois, use o botão **Configurar** na
integração (não precisa remover e adicionar de novo).

### Se o IP do multiroom mudar

Vá no card da integração → menu (⋮) → **Reconfigurar**, e informe o novo
IP. Isso atualiza a conexão sem apagar a integração — nomes de zonas,
entradas e o histórico das entidades são mantidos.

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

## Diagnóstico de erros

- **Falha de conexão** (na tela de configuração ou ao carregar a
  integração): a mensagem real (timeout, recusada, sem rota etc.) fica
  registrada nos logs do Home Assistant (Configurações → Sistema → Logs),
  além do aviso genérico que aparece na tela.
- **Comando recusado pelo aparelho** (ex.: zona inválida): em vez de uma
  exceção genérica, a integração traduz os códigos de erro do protocolo
  (seção 1.3.8 do manual) em mensagens específicas — "aparelho precisa
  estar ligado", "zona inválida ou valor fora do intervalo", etc. — que
  aparecem na notificação de erro do Home Assistant. O estado da zona
  também é ressincronizado automaticamente logo em seguida, corrigindo
  qualquer suposição otimista que tenha ficado errada.

## Testes

A suíte de testes (`tests/`) valida o parsing do protocolo e a lógica de
estado por zona usando os exemplos de bytes reais do próprio manual da AAT
(ex.: as respostas de `GETALL` para PMR-7 e PMR-6), rodando contra um
servidor TCP fake local — não precisa de hardware físico nem de uma
instância do Home Assistant rodando.

```bash
pip install -r requirements_test.txt
pytest
```

51 testes, cobrindo: framing/sequencial/GETALL/mensagens não
solicitadas/timeouts do protocolo (`test_api_protocol.py`), parsing de
estado por zona e todos os handlers de push (`test_device_state.py`), a
tradução dos erros do protocolo em mensagens amigáveis, incluindo a
ressincronização automática após uma falha (`test_device_errors.py`), e as
propriedades/seleção de fonte do `media_player` (`test_media_player.py`).

Uma exceção documentada: o manual nunca mostra os bytes exatos de uma
resposta de erro (só o significado de cada código, ex. "17 - zona
inválida"). O teste `test_error_code_reply_raises_command_error` deixa
explícito que a integração assume o formato `[r001 17]` — isso ainda
precisa ser confirmado contra um aparelho real.

## Limitações conhecidas / próximos passos possíveis

- Não implementa tons (bass/treble), balanço, ganho de pré-amp, modo
  festa/agrupamento de zonas nem o streamer embutido dos modelos PMR-9 a
  PMR-13 — pode ser adicionado depois como entidades `number`/`switch`
  adicionais, sem precisar redesenhar o que já existe.
- A contagem de entradas por modelo (usada só para sugerir quantos
  switches criar no primeiro setup) vem de uma tabela estática baseada na
  capa do manual; se o seu modelo específico tiver menos entradas ligadas
  fisicamente do que o padrão do modelo, os switches extras simplesmente
  não farão efeito (o próprio aparelho ignora comandos para entradas
  inexistentes).

## Histórico: troca de botões por switches na seleção de entrada

Até a v0.6.0, a seleção de entrada era feita por `button` (ação simples,
sem estado). A partir da v0.7.0 isso virou `switch` (liga/desliga com
estado real), porque só assim o Home Assistant consegue pintar o ícone da
entrada ativa automaticamente — `button` não tem conceito de "ligado".
Se você atualizou de uma versão anterior, os botões antigos (`button.*`)
somem da lista de entidades fornecidas pela integração e ficam
"indisponíveis" no registro do Home Assistant; pode removê-los manualmente
em Configurações → Dispositivos e serviços → Entidades (filtre por
"indisponível"). Os switches novos (`switch.*_input_*`) são criados com
nomes/entity_id novos automaticamente.

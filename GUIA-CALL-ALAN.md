# DataCrazy Gateway — Guia Completo para Call com Alan

## O que é

Gateway server-side que recebe eventos do CRM (DataCrazy, N8N, qualquer sistema) e dispara para a **Meta Conversions API (CAPI)** v21.0. Complementa o pixel client-side do site, melhorando match rate e atribuição.

---

## URLs

| Recurso | URL |
|---------|-----|
| Dashboard | https://datacrazy-api.jz9bd8.easypanel.host/ |
| Swagger Docs | https://datacrazy-api.jz9bd8.easypanel.host/docs |
| ReDoc | https://datacrazy-api.jz9bd8.easypanel.host/redoc |
| API Base | https://datacrazy-api.jz9bd8.easypanel.host/api |

**Master Key atual**: `dc-master-alan-2026`

---

## Como Conectar no Dashboard

1. Abrir https://datacrazy-api.jz9bd8.easypanel.host/
2. No canto superior direito, campo "Master Key" → digitar `dc-master-alan-2026`
3. Clicar **Conectar**
4. Bolinha verde = conectado

---

## Campos de um Cliente

Ao criar ou editar um cliente, cada campo significa:

| Campo | Obrigatório | O que é | Onde encontrar |
|-------|-------------|---------|----------------|
| **Nome** | Sim | Nome de identificação do cliente (livre) | Escolher qualquer nome descritivo |
| **Meta Pixel ID** | Sim | ID numérico do pixel Meta (Facebook) | Meta Business Suite → Events Manager → Data Sources → clicar no pixel → ID aparece no topo (número tipo `230237130670283`) |
| **Meta Access Token** | Sim | Token de autenticação server-side para enviar eventos via CAPI | Meta Business Suite → Business Settings → System Users → selecionar user → Generate Token (permissões: `ads_management`, `business_management`) |
| **Eventos Habilitados** | Sim | Quais tipos de evento esse cliente pode disparar | Marcar os checkboxes. Os mais comuns: `Purchase` (venda), `Lead` (lead qualificado) |
| **Token DataCrazy CRM** | Não | API key do CRM DataCrazy do cliente (para puxar dados automático) | Painel DataCrazy → Configurações → API |

### Sobre o Meta Access Token

- Começa com `EAA` (ex: `EAATvcmlRNpc...`)
- Pode ser **System User Token** (recomendado, não expira) ou **User Token** (expira em ~60 dias)
- O token precisa ter permissão no pixel que foi informado
- Se o token estiver errado, os eventos vão com status `error` e a mensagem diz exatamente o problema

### Sobre o Pixel ID

- É um número (ex: `230237130670283`)
- Cada Business Manager pode ter múltiplos pixels
- O pixel do site (client-side) e o CAPI (server-side) usam o MESMO Pixel ID
- Ter os dois (client + server) é o que a Meta recomenda — melhora o match rate de 40-60% para 80-95%

---

## Tipos de Evento

| Evento Meta | Quando usar | Evento CRM equivalente |
|-------------|-------------|----------------------|
| **Purchase** | Venda confirmada, pagamento aprovado | `deal_won`, `deal_closed`, `negocio_ganho`, `payment_confirmed`, `pago` |
| **Lead** | Lead qualificado, novo cadastro | `lead_qualified`, `lead_qualificado`, `new_lead`, `novo_lead`, `qualificado` |
| **ViewContent** | Visualização de página/produto | - |
| **AddToCart** | Adicionou ao carrinho | - |
| **InitiateCheckout** | Iniciou checkout | - |
| **CompleteRegistration** | Completou cadastro/registro | - |

A API faz a tradução automática: se o CRM mandar `deal_won`, a API converte para `Purchase` antes de enviar pra Meta.

---

## Dados do Usuário (user_data)

Esses são os dados que a Meta usa para fazer **match** entre o evento server-side e o usuário no Facebook/Instagram.

| Campo | Formato | Importância | Exemplo |
|-------|---------|-------------|---------|
| **email** | texto livre (a API faz hash SHA-256 automático) | ALTA — principal identificador | `joao@empresa.com` |
| **phone** | só números, com código país | ALTA — segundo melhor match | `5547999220055` |
| **first_name** | texto | MÉDIA | `Joao` |
| **last_name** | texto | MÉDIA | `Silva` |
| **city** | texto | BAIXA | `Florianopolis` |
| **state** | sigla | BAIXA | `SC` |
| **country** | código ISO | BAIXA | `br` |
| **external_id** | ID do lead/deal no CRM | BAIXA — deduplicação | `12345` |

**Importante**: A API faz SHA-256 automático em TODOS os campos antes de enviar pra Meta. Nunca sai dado em texto limpo. Isso é obrigatório pela Meta e compatível com LGPD.

**Quanto mais campos, melhor o match**. Mas no mínimo precisa de email OU telefone.

---

## Dados do Evento (custom_data)

| Campo | Tipo | Quando usar | Exemplo |
|-------|------|-------------|---------|
| **value** | número | Valor monetário (vendas) | `1500.00` |
| **currency** | texto | Moeda (padrão BRL) | `BRL` |
| **content_name** | texto | Nome do produto/serviço | `Aula de Surf Privada` |

O `value` + `currency` é essencial para eventos `Purchase` — permite calcular ROAS no Meta Ads Manager.

---

## Source URL

URL de onde o evento se originou. Exemplos:
- `https://site.com/obrigado` (página de obrigado pós-compra)
- `https://site.com/formulario` (formulário de lead)
- `https://crm.datacrazy.io` (veio direto do CRM)

A Meta usa isso para atribuição. Não é obrigatório mas melhora a qualidade do dado.

---

## Modo Teste

Quando marcado, os eventos vão com `test_event_code: TEST12345`. Isso faz os eventos aparecerem na aba **Test Events** do Meta Events Manager, sem poluir dados reais de campanha.

**Na call**: deixar modo teste ligado. Depois de validar, desmarcar para produção.

Para ver os test events:
1. Meta Business Suite → Events Manager
2. Selecionar o pixel
3. Aba **Test Events**
4. Os eventos aparecem em tempo real (delay de ~5 segundos)

---

## 3 Formas de Enviar Eventos

### 1. Dashboard (manual — para testes)
- Aba "Testar Disparo" → preencher → clicar Disparar
- Resultado aparece na hora com JSON completo

### 2. API Track (programático — controle total)
```
POST /api/events/track
Headers: X-API-Key: dc-master-alan-2026

{
  "client_id": "uuid-do-cliente",
  "event_type": "Purchase",
  "user_data": {
    "email": "lead@empresa.com",
    "phone": "5547999220055"
  },
  "custom_data": {
    "value": 1500.00,
    "currency": "BRL"
  },
  "test_mode": false
}
```

### 3. Webhook CRM (automático — integração com DataCrazy/N8N)
```
POST /api/events/webhook
Headers: X-API-Key: dc_xxxxx (API key do cliente)

{
  "event": "deal_won",
  "client_identifier": "Nome do Cliente",
  "data": {
    "deal_id": "123",
    "value": 1500.00,
    "currency": "BRL",
    "email": "lead@empresa.com",
    "phone": "5547999220055",
    "name": "Joao Silva"
  }
}
```

O webhook traduz automaticamente os nomes dos campos e eventos do CRM para o formato Meta.

---

## Fluxo WhatsApp → CRM → Meta

```
Lead no WhatsApp
    ↓
CRM DataCrazy registra lead
    ↓
Lead qualificado → CRM manda webhook: {"event": "lead_qualified", "data": {...}}
    ↓
DataCrazy Gateway recebe, traduz para "Lead", hasheia dados, envia pra Meta CAPI
    ↓
Meta recebe evento server-side, faz match com usuário Facebook/Instagram
    ↓
Atribuição correta no Ads Manager (ROAS, CPA real)
```

## Fluxo Formulário → Meta

```
Usuário preenche formulário no site
    ↓
Pixel client-side dispara evento ViewContent/Lead (browser)
    ↓
Backend do site (ou CRM) recebe os dados
    ↓
Backend manda para DataCrazy Gateway via webhook
    ↓
Gateway envia mesmo evento via CAPI (server-side)
    ↓
Meta recebe AMBOS (client + server) e faz deduplicação automática pelo event_id
    ↓
Match rate sobe de ~50% para ~90%
```

---

## Roteiro da Call (10 minutos)

### 1. Mostrar Dashboard (2 min)
- Abrir https://datacrazy-api.jz9bd8.easypanel.host/
- Conectar com master key
- Mostrar métricas: X eventos enviados, taxa de sucesso
- Mostrar lista de clientes com token validado (verde)

### 2. Criar Cliente do Alan (3 min)
- Aba Clientes → + Novo Cliente
- Nome: nome do cliente real do Alan
- Pixel ID: pedir pro Alan (ou usar o demo)
- Meta Access Token: pedir pro Alan colar o token
- Selecionar eventos: Purchase + Lead no mínimo
- Criar → API Key gerada automaticamente

### 3. Disparar Evento Teste (3 min)
- Aba "Testar Disparo"
- Selecionar o cliente recém criado
- Tipo: Lead
- Preencher email e telefone de teste
- Modo Teste: marcado
- Clicar Disparar
- Mostrar resultado verde: `events_received: 1`
- Abrir Meta Events Manager → Test Events → mostrar evento chegando

### 4. Explicar Integração CRM (2 min)
- Mostrar exemplo de webhook
- Explicar: "No DataCrazy, quando um deal muda de status, vocês fazem um POST pra essa URL com os dados. A API traduz e manda pra Meta automaticamente."
- Mostrar Swagger docs: toda a API documentada

---

## Se der erro na call

| Erro | Causa | Solução |
|------|-------|---------|
| `Invalid OAuth access token` | Token Meta errado ou expirado | Gerar novo token no Meta Business Suite |
| `The access token could not be decrypted` | Token corrompido (copiou incompleto) | Copiar token inteiro de novo |
| `Event 'X' not enabled for this client` | Tipo de evento não habilitado | Editar cliente → marcar checkbox do evento |
| `Client not found` | API key errada ou client_identifier não bate | Verificar API key ou nome do cliente |
| `Cannot parse access token` | Token não é formato Meta (não começa com EAA) | Verificar se copiou o token correto |

---

## Autenticação

Existem 2 níveis de API key:

| Tipo | Formato | Acesso |
|------|---------|--------|
| **Master Key** | `dc-master-alan-2026` | Tudo: criar/editar/deletar clientes, ver todos eventos, disparar para qualquer cliente |
| **Client API Key** | `dc_xxxx...` (gerada auto) | Apenas: disparar eventos do próprio cliente, ver histórico próprio |

Na produção, cada cliente do Alan recebe sua própria API Key. O CRM usa essa key no webhook.

---

## Arquitetura

```
[Site/App]  →  Pixel JS (client-side)  →  Meta Pixel
     ↓
[CRM DataCrazy]  →  Webhook  →  DataCrazy Gateway API  →  Meta CAPI (server-side)
                                       ↓
                                  PostgreSQL (log de eventos)
                                       ↓
                                  Dashboard (métricas)
```

- **FastAPI** (Python) — performático, async
- **PostgreSQL** — log de todos os eventos
- **Meta Graph API v21.0** — versão mais recente
- **SHA-256** — hashing automático de PII
- **Deploy**: EasyPanel (Docker) com HTTPS automático

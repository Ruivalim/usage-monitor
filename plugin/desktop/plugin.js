import { host, haptic, useQuery, atom, useValue } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

const STATUS_LABEL = {
  ok: 'ok',
  warning: 'warn',
  error: 'erro',
  rate_limited: '429',
  quota_exhausted: 'quota',
  unavailable: 'n/a',
  unknown: '?'
}

const STATUS_CLASS = {
  ok: 'text-(--ui-success)',
  warning: 'text-(--ui-warning)',
  error: 'text-(--ui-danger)',
  rate_limited: 'text-(--ui-warning)',
  quota_exhausted: 'text-(--ui-danger)',
  unavailable: 'text-(--ui-text-quaternary)',
  unknown: 'text-(--ui-text-tertiary)'
}

function money(balance) {
  if (!balance || typeof balance.amount !== 'number') return '—'
  return `${balance.amount.toFixed(2)} ${balance.currency || ''}`.trim()
}

function windows(provider) {
  const ws = provider.windows || []
  if (!ws.length) return '—'
  return ws.map((w) => {
    if (typeof w.remaining_percent === 'number') return `${w.label}: ${Math.round(w.remaining_percent)}% left`
    if (typeof w.used_percent === 'number') return `${w.label}: ${Math.round(w.used_percent)}% used`
    return w.label
  }).join(' · ')
}

function useStatus() {
  return useQuery({
    queryKey: ['api-usage-monitor', 'status'],
    queryFn: () => ctxRef.rest('/status'),
    refetchInterval: 120000,
    staleTime: 30000
  })
}

function useConfig() {
  return useQuery({
    queryKey: ['api-usage-monitor', 'config'],
    queryFn: () => ctxRef.rest('/config'),
    staleTime: 300000
  })
}

// Same predicate as the standalone dashboard (web.py): the hermes-auth adapter
// emits one `auth:<provider>` entry per credential, which is noise next to the
// balance/quota rows most of the time.
const isAuthEntry = (p) => String(p?.id || '').startsWith('auth:')

const visibleProviders = (data, showAuth) =>
  (data?.providers || []).filter((p) => showAuth || !isAuthEntry(p))

// Alerts carry the provider id, so hidden rows must not keep raising the chip
// count — otherwise the chip reports alerts with no visible row behind them.
const visibleAlerts = (data, showAuth) =>
  (data?.alerts || []).filter((a) => showAuth || !String(a?.provider || '').startsWith('auth:'))

const STORAGE_SHOW_AUTH = 'showAuth'

// null = follow config.yaml's dashboard.show_auth; true/false = the user used
// the pane toggle, which wins and is remembered across restarts.
const showAuthOverride = atom(null)

function resolveShowAuth(config, override) {
  if (override === true || override === false) return override
  return !!(config?.dashboard || {}).show_auth
}

function useShowAuth() {
  const config = useConfig().data
  return resolveShowAuth(config, useValue(showAuthOverride))
}

function setShowAuth(value) {
  showAuthOverride.set(value)
  ctxRef.storage.set(STORAGE_SHOW_AUTH, value)
}

let ctxRef = null

function Chip() {
  const q = useStatus()
  const showAuth = useShowAuth()
  const data = q.data
  const alerts = visibleAlerts(data, showAuth).length
  const overall = data?.overall || (q.isError ? 'error' : 'unknown')
  return jsx('button', {
    type: 'button',
    className: `px-1.5 text-[0.6875rem] ${STATUS_CLASS[overall] || STATUS_CLASS.unknown}`,
    title: q.isLoading ? 'API Usage: carregando' : `API Usage: ${overall}`,
    onClick: () => {
      haptic('tap')
      host.notify({ kind: alerts ? 'warning' : 'info', message: alerts ? `API Usage: ${alerts} alerta(s)` : 'API Usage: tudo ok' })
    },
    children: `APIs: ${alerts ? `${alerts} alertas` : STATUS_LABEL[overall] || overall}`
  })
}

function ProviderRow({ provider }) {
  const status = provider.status || 'unknown'
  return jsxs('tr', {
    className: 'border-b border-(--ui-stroke-secondary)',
    children: [
      jsx('td', { className: 'py-1.5 pr-3 font-medium', children: provider.label || provider.id }),
      jsx('td', { className: `py-1.5 pr-3 ${STATUS_CLASS[status] || STATUS_CLASS.unknown}`, children: status }),
      jsx('td', { className: 'py-1.5 pr-3 tabular-nums', children: money(provider.balance) }),
      jsx('td', { className: 'py-1.5 pr-3 text-(--ui-text-secondary)', children: windows(provider) }),
      jsx('td', { className: 'py-1.5 text-(--ui-text-tertiary)', children: provider.message || (provider.details || []).slice(0, 1).join(' · ') || '—' })
    ]
  })
}

function ApiUsagePane() {
  const q = useStatus()
  const showAuth = useShowAuth()
  const data = q.data
  const refresh = async () => {
    haptic('tap')
    await ctxRef.rest('/refresh', { method: 'POST', timeoutMs: 45000 })
    await q.refetch()
    host.notify({ kind: 'info', message: 'API Usage atualizado' })
  }
  const copyJson = async () => {
    const text = JSON.stringify(data || {}, null, 2)
    await navigator.clipboard.writeText(text)
    host.notify({ kind: 'info', message: 'JSON copiado' })
  }

  if (q.isLoading) {
    return jsx('div', { className: 'p-3 text-sm text-(--ui-text-tertiary)', children: 'Consultando providers…' })
  }
  if (q.isError) {
    return jsx('div', { className: 'p-3 text-sm text-(--ui-danger)', children: `Erro no plugin: ${q.error?.message || q.error}` })
  }

  const providers = visibleProviders(data, showAuth)
  const alerts = visibleAlerts(data, showAuth)
  const hiddenAuth = (data?.providers || []).filter(isAuthEntry).length
  return jsxs('div', {
    className: 'flex h-full flex-col gap-3 p-3 text-sm',
    children: [
      jsxs('div', { className: 'flex items-center justify-between gap-2', children: [
        jsxs('div', { children: [
          jsx('div', { className: 'font-medium', children: 'API Usage Monitor' }),
          jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: `overall: ${data?.overall || 'unknown'} · ${data?.checked_at || ''}` })
        ] }),
        jsxs('div', { className: 'flex gap-2', children: [
          hiddenAuth || showAuth ? jsx('button', {
            type: 'button',
            className: 'rounded border border-(--ui-stroke-secondary) px-2 py-1 text-xs hover:bg-(--ui-fill-hover)',
            title: 'Mostrar/ocultar os providers auth:* do hermes-auth',
            onClick: () => { haptic('tap'); setShowAuth(!showAuth) },
            children: showAuth ? `Ocultar auth (${hiddenAuth})` : `Mostrar auth (${hiddenAuth})`
          }) : null,
          jsx('button', { type: 'button', className: 'rounded border border-(--ui-stroke-secondary) px-2 py-1 text-xs hover:bg-(--ui-fill-hover)', onClick: refresh, children: 'Refresh' }),
          jsx('button', { type: 'button', className: 'rounded border border-(--ui-stroke-secondary) px-2 py-1 text-xs hover:bg-(--ui-fill-hover)', onClick: copyJson, children: 'Copy JSON' })
        ] })
      ] }),
      alerts.length ? jsx('div', { className: 'rounded border border-(--ui-warning) p-2 text-xs text-(--ui-warning)', children: alerts.map((a) => `${a.provider}: ${a.message}`).join(' · ') }) : null,
      jsx('div', { className: 'overflow-auto', children: jsx('table', { className: 'w-full border-collapse text-left text-xs', children: jsxs('tbody', { children: providers.map((p) => jsx(ProviderRow, { provider: p }, p.id)) }) }) }),
      jsx('div', { className: 'mt-auto text-xs text-(--ui-text-quaternary)', children: `Adapters externos: ${data?.meta?.external_adapter_dir || '~/.config/usagemon/adapters'}` })
    ]
  })
}

export default {
  id: 'api-usage-monitor',
  name: 'API Usage Monitor',
  register(ctx) {
    ctxRef = ctx
    // null (the default) means "follow dashboard.show_auth from config.yaml",
    // so a fresh install hides auth:* without the user touching anything.
    const stored = ctx.storage.get(STORAGE_SHOW_AUTH, null)
    showAuthOverride.set(stored === true || stored === false ? stored : null)
    ctx.register({
      id: 'pane',
      area: 'panes',
      title: 'API Usage',
      data: { placement: 'right', width: '420px' },
      render: () => jsx(ApiUsagePane, {})
    })
    ctx.register({
      id: 'chip',
      area: 'statusBar.right',
      order: 125,
      render: () => jsx(Chip, {})
    })
    ctx.register({
      id: 'refresh-command',
      area: 'palette',
      data: {
        id: 'api-usage-refresh',
        label: 'API Usage: Refresh',
        keywords: ['api', 'usage', 'credits', 'quota', 'limits'],
        run: async () => {
          await ctx.rest('/refresh', { method: 'POST', timeoutMs: 45000 })
          host.notify({ kind: 'info', message: 'API Usage atualizado' })
        }
      }
    })
  }
}

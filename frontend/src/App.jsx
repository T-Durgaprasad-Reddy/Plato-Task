import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';

const API_BASE = import.meta.env.VITE_API_URL || '/api/v1';

// ─── helpers ──────────────────────────────────────────────────────────────────

const fmt = (paise) => `₹${(paise / 100).toFixed(2)}`;

const STATUS_META = {
  pending:    { bg: 'bg-amber-400/10',   text: 'text-amber-400',   border: 'border-amber-400/30',   dot: 'bg-amber-400'   },
  processing: { bg: 'bg-blue-400/10',    text: 'text-blue-400',    border: 'border-blue-400/30',    dot: 'bg-blue-400 animate-pulse' },
  completed:  { bg: 'bg-emerald-400/10', text: 'text-emerald-400', border: 'border-emerald-400/30', dot: 'bg-emerald-400' },
  failed:     { bg: 'bg-red-400/10',     text: 'text-red-400',     border: 'border-red-400/30',     dot: 'bg-red-400'     },
};

const ENTRY_META = {
  CREDIT:  { text: 'text-emerald-400', sign: '+', label: 'Credit'  },
  DEBIT:   { text: 'text-red-400',     sign: '−', label: 'Debit'   },
  HOLD:    { text: 'text-amber-400',   sign: '−', label: 'Hold'    },
  RELEASE: { text: 'text-blue-400',    sign: '+', label: 'Release' },
};

// ─── tiny components ─────────────────────────────────────────────────────────

function StatusBadge({ s }) {
  const m = STATUS_META[s] ?? STATUS_META.pending;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold border ${m.bg} ${m.text} ${m.border}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${m.dot}`} />
      {s}
    </span>
  );
}

function Card({ children, className = '' }) {
  return (
    <div className={`bg-slate-800/60 border border-slate-700/50 rounded-2xl backdrop-blur-sm shadow-xl ${className}`}>
      {children}
    </div>
  );
}

function SectionHeader({ label, count }) {
  return (
    <div className="flex items-center justify-between mb-4">
      <h2 className="text-sm font-semibold text-slate-300 uppercase tracking-widest">{label}</h2>
      {count != null && (
        <span className="text-xs bg-slate-700 text-slate-400 rounded-full px-2 py-0.5">{count}</span>
      )}
    </div>
  );
}

function Spinner() {
  return (
    <svg className="animate-spin w-4 h-4" viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
    </svg>
  );
}

function EmptyRow({ cols, msg }) {
  return (
    <tr>
      <td colSpan={cols} className="py-10 text-center text-slate-500 text-sm italic">{msg}</td>
    </tr>
  );
}

// ─── main app ────────────────────────────────────────────────────────────────

export default function App() {
  const [merchants, setMerchants]       = useState([]);
  const [selectedId, setSelectedId]     = useState(null);
  const [balance, setBalance]           = useState(null);
  const [bankAccounts, setBankAccounts] = useState([]);
  const [ledger, setLedger]             = useState([]);
  const [payouts, setPayouts]           = useState([]);

  // form state
  const [amount, setAmount]     = useState('');
  const [bankId, setBankId]     = useState('');
  const [submitting, setSubmit] = useState(false);
  const [formMsg, setFormMsg]   = useState(null); // { type: 'error'|'ok', text }

  // ── fetch helpers ────────────────────────────────────────────────────────

  const loadMerchants = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API_BASE}/merchants/`);
      setMerchants(data);
      if (!selectedId && data.length > 0) setSelectedId(data[0].id);
    } catch { /* silent */ }
  }, [selectedId]);

  const loadMerchantData = useCallback(async (id) => {
    if (!id) return;
    try {
      const [bal, accs, led, pays] = await Promise.all([
        axios.get(`${API_BASE}/merchants/${id}/balance/`),
        axios.get(`${API_BASE}/merchants/${id}/bank-accounts/`),
        axios.get(`${API_BASE}/merchants/${id}/ledger/`),
        axios.get(`${API_BASE}/payouts/?merchant_id=${id}`),
      ]);
      setBalance(bal.data);
      setBankAccounts(accs.data);
      if (accs.data.length > 0 && !bankId) setBankId(String(accs.data[0].id));
      setLedger(led.data);
      setPayouts(pays.data);
    } catch { /* silent */ }
  }, [bankId]);

  // initial load
  useEffect(() => { loadMerchants(); }, []);

  // load merchant data + 3s poll
  useEffect(() => {
    if (!selectedId) return;
    loadMerchantData(selectedId);
    const interval = setInterval(() => loadMerchantData(selectedId), 3000);
    return () => clearInterval(interval);
  }, [selectedId]);

  // reset bank id when merchant changes
  useEffect(() => { setBankId(''); }, [selectedId]);

  // ── submit payout ─────────────────────────────────────────────────────────

  const handlePayout = async (e) => {
    e.preventDefault();
    if (!bankId) { setFormMsg({ type: 'error', text: 'Select a bank account' }); return; }
    setSubmit(true);
    setFormMsg(null);
    try {
      await axios.post(
        `${API_BASE}/payouts/`,
        { amount_paise: parseInt(amount), bank_account_id: parseInt(bankId), merchant_id: selectedId },
        { headers: { 'Idempotency-Key': crypto.randomUUID() } },
      );
      setAmount('');
      setFormMsg({ type: 'ok', text: 'Payout requested!' });
      loadMerchantData(selectedId);
    } catch (err) {
      const msg = err.response?.data?.error ?? 'An error occurred';
      setFormMsg({ type: 'error', text: Array.isArray(msg) ? msg.join(' ') : String(msg) });
    } finally {
      setSubmit(false);
    }
  };

  // ── derived ──────────────────────────────────────────────────────────────

  const merchant = merchants.find(m => m.id === selectedId);

  // ── render ───────────────────────────────────────────────────────────────

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 font-sans antialiased">
      {/* gradient blobs */}
      <div className="fixed inset-0 pointer-events-none overflow-hidden -z-10">
        <div className="absolute -top-40 -left-40 w-96 h-96 bg-blue-600/10 rounded-full blur-3xl" />
        <div className="absolute top-1/2 -right-40 w-80 h-80 bg-emerald-600/10 rounded-full blur-3xl" />
      </div>

      {/* nav */}
      <header className="border-b border-slate-800 bg-slate-950/80 backdrop-blur sticky top-0 z-20">
        <div className="max-w-7xl mx-auto px-6 h-14 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-blue-500 to-emerald-500 flex items-center justify-center text-xs font-bold">P</div>
            <span className="font-semibold tracking-tight">Playto Payout Engine</span>
          </div>
          {/* merchant selector */}
          <select
            value={selectedId ?? ''}
            onChange={e => setSelectedId(Number(e.target.value))}
            className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            {merchants.map(m => (
              <option key={m.id} value={m.id}>{m.name}</option>
            ))}
          </select>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-8 space-y-6">

        {/* balance cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {[
            { label: 'Available',   value: balance?.available_balance ?? 0, color: 'text-emerald-400', glow: 'shadow-emerald-900/30' },
            { label: 'Held',        value: balance?.held_balance ?? 0,      color: 'text-amber-400',   glow: 'shadow-amber-900/30'   },
            { label: 'Payouts',     value: payouts.length,                  color: 'text-blue-400',    glow: '', raw: true },
            { label: 'Ledger Rows', value: ledger.length,                   color: 'text-slate-300',   glow: '', raw: true },
          ].map(({ label, value, color, glow, raw }) => (
            <Card key={label} className={`p-5 shadow-lg ${glow}`}>
              <div className="text-xs text-slate-500 uppercase tracking-widest mb-1">{label}</div>
              <div className={`text-2xl font-bold font-mono ${color}`}>
                {raw ? value : fmt(value)}
              </div>
              {!raw && <div className="text-xs text-slate-600 mt-1">{value} paise</div>}
            </Card>
          ))}
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">

          {/* ── payout form ─────────────────────────────────────────────── */}
          <Card className="p-6 h-fit">
            <SectionHeader label="New Payout" />
            <form onSubmit={handlePayout} className="space-y-4">
              <div>
                <label className="block text-xs text-slate-400 mb-1.5">Amount (paise)</label>
                <input
                  id="payout-amount"
                  type="number"
                  min="1"
                  value={amount}
                  onChange={e => setAmount(e.target.value)}
                  placeholder="e.g. 5000  →  ₹50"
                  required
                  className="w-full bg-slate-900 border border-slate-700 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition"
                />
                {amount && !isNaN(amount) && (
                  <div className="text-xs text-slate-500 mt-1">{fmt(parseInt(amount))}</div>
                )}
              </div>
              <div>
                <label className="block text-xs text-slate-400 mb-1.5">Bank Account</label>
                <select
                  id="payout-bank"
                  value={bankId}
                  onChange={e => setBankId(e.target.value)}
                  required
                  className="w-full bg-slate-900 border border-slate-700 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition"
                >
                  <option value="">— select account —</option>
                  {bankAccounts.map(ba => (
                    <option key={ba.id} value={ba.id}>
                      {ba.account_number} · {ba.ifsc}
                    </option>
                  ))}
                </select>
              </div>

              {formMsg && (
                <div className={`text-xs rounded-xl px-4 py-2.5 border ${
                  formMsg.type === 'error'
                    ? 'bg-red-400/10 text-red-400 border-red-400/20'
                    : 'bg-emerald-400/10 text-emerald-400 border-emerald-400/20'
                }`}>
                  {formMsg.text}
                </div>
              )}

              <button
                id="submit-payout"
                type="submit"
                disabled={submitting}
                className="w-full flex items-center justify-center gap-2 bg-gradient-to-r from-blue-600 to-blue-500 hover:from-blue-500 hover:to-blue-400 disabled:from-slate-700 disabled:to-slate-700 text-white font-semibold py-2.5 rounded-xl transition-all shadow-lg shadow-blue-900/30"
              >
                {submitting ? <><Spinner /> Processing…</> : 'Initiate Payout'}
              </button>
            </form>

            {/* bank account list */}
            {bankAccounts.length > 0 && (
              <div className="mt-6 pt-6 border-t border-slate-700/50 space-y-2">
                <div className="text-xs text-slate-500 uppercase tracking-widest mb-2">Accounts</div>
                {bankAccounts.map(ba => (
                  <div key={ba.id} className="flex items-center justify-between text-xs text-slate-400 bg-slate-900/60 rounded-lg px-3 py-2">
                    <span className="font-mono">{ba.account_number}</span>
                    <span className="text-slate-600">{ba.ifsc}</span>
                  </div>
                ))}
              </div>
            )}
          </Card>

          {/* ── right column ─────────────────────────────────────────────── */}
          <div className="lg:col-span-2 space-y-6">

            {/* payout history */}
            <Card className="p-6">
              <SectionHeader label="Payout History" count={payouts.length} />
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-slate-500 text-xs uppercase tracking-wider border-b border-slate-700/50">
                      <th className="pb-3 text-left font-medium">ID</th>
                      <th className="pb-3 text-left font-medium">Amount</th>
                      <th className="pb-3 text-left font-medium">Bank</th>
                      <th className="pb-3 text-left font-medium">Status</th>
                      <th className="pb-3 text-right font-medium">Time</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-700/30">
                    {payouts.length === 0 && <EmptyRow cols={5} msg="No payouts yet" />}
                    {payouts.map(p => (
                      <tr key={p.id} className="hover:bg-slate-700/20 transition-colors">
                        <td className="py-3 font-mono text-xs text-slate-500">#{p.id}</td>
                        <td className="py-3 font-semibold">{fmt(p.amount_paise)}</td>
                        <td className="py-3 text-xs text-slate-500 font-mono">
                          {p.bank_account_display?.split(' ')[0] ?? '—'}
                        </td>
                        <td className="py-3"><StatusBadge s={p.status} /></td>
                        <td className="py-3 text-right text-xs text-slate-500">
                          {new Date(p.created_at).toLocaleTimeString()}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>

            {/* ledger entries */}
            <Card className="p-6">
              <SectionHeader label="Ledger Entries" count={`last ${Math.min(ledger.length, 20)}`} />
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-slate-500 text-xs uppercase tracking-wider border-b border-slate-700/50">
                      <th className="pb-3 text-left font-medium">Type</th>
                      <th className="pb-3 text-right font-medium">Amount</th>
                      <th className="pb-3 text-left font-medium pl-4">Ref</th>
                      <th className="pb-3 text-right font-medium">Time</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-700/30">
                    {ledger.length === 0 && <EmptyRow cols={4} msg="No ledger entries" />}
                    {ledger.slice(0, 20).map(e => {
                      const m = ENTRY_META[e.entry_type] ?? {};
                      return (
                        <tr key={e.id} className="hover:bg-slate-700/20 transition-colors">
                          <td className="py-2.5">
                            <span className={`text-xs font-semibold uppercase tracking-wide ${m.text}`}>
                              {m.label ?? e.entry_type}
                            </span>
                          </td>
                          <td className={`py-2.5 text-right font-mono text-sm font-semibold ${m.text}`}>
                            {m.sign}{fmt(e.amount_paise)}
                          </td>
                          <td className="py-2.5 pl-4 text-xs text-slate-500 font-mono">
                            {e.ref_type}:{e.ref_id}
                          </td>
                          <td className="py-2.5 text-right text-xs text-slate-500">
                            {new Date(e.created_at).toLocaleTimeString()}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>
        </div>
      </main>

      <footer className="text-center py-6 text-xs text-slate-700 border-t border-slate-800 mt-8">
        Playto Payout Engine — Ledger-based, append-only, SELECT FOR UPDATE concurrency
      </footer>
    </div>
  );
}

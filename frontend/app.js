/* app.js — Guidewire AI Risk Assessment v3 */

const API_BASE = 'http://localhost:8000';

// ── Sample data ───────────────────────────────────────────────────────────────
const SAMPLES = {
  low:    'The insurer will compensate losses described in the policy when documentation confirms eligibility within 30 calendar days of claim receipt.',
  medium: 'Claims will be reviewed and may be paid subject to verification of eligibility criteria and available documentation.',
  high:   'Coverage may apply in certain situations subject to evaluation by the insurer, depending on internal review and surrounding circumstances.',
};

const SUBMIT_SAMPLE_CLAUSES = [
  'The insurer will compensate losses described in the policy when documentation confirms eligibility within 30 calendar days of claim receipt.',
  'Coverage may apply in certain situations subject to evaluation by the insurer, depending on internal review and surrounding circumstances.',
  'Claims may be approved or declined based on the insurer\'s sole interpretation of the situation.',
  'Covered damages will be reimbursed in accordance with the policy schedule within 15 business days.',
  'The insurer reserves the right to modify coverage terms at any time without prior notice to the policyholder.',
  'The company does not cover losses arising from events deemed to fall under certain excluded categories as determined by internal evaluation.',
].join('\n');

const FLAG_LABELS = {
  ambiguous_trigger:  'Ambiguous trigger language',
  insurer_discretion: 'Insurer sole discretion',
  blanket_exclusion:  'Blanket exclusion',
  conditional_payout: 'Conditional payout',
  vague_conditions:   'Vague conditions',
  unilateral_change:  'Unilateral change clause',
};

// ── State ─────────────────────────────────────────────────────────────────────
let _currentSubmissionId = null;
let _bulkData = [];

// ── Nav tab switching ─────────────────────────────────────────────────────────
document.querySelectorAll('.nav-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    tab.classList.add('active');
    const panel = document.getElementById(`tab-${tab.dataset.tab}`);
    if (panel) panel.classList.add('active');

    // Auto-load data on tab open
    if (tab.dataset.tab === 'queue')  loadQueues();
    if (tab.dataset.tab === 'audit')  loadAuditLog();
  });
});

function switchSubTab(name) {
  document.querySelectorAll('.sub-tab').forEach(t => {
    if (t.dataset.subtab === name) t.classList.add('active');
    else t.classList.remove('active');
  });
  ['uw', 'legal'].forEach(n => {
    const el = document.getElementById(`subtab-${n}`);
    if (el) el.style.display = n === name ? 'block' : 'none';
  });
}

function switchDevTab(name) {
  document.querySelectorAll('[data-subtab]').forEach(t => {
    if (t.dataset.subtab === name) t.classList.add('active');
    else t.classList.remove('active');
  });
  ['single', 'bulkdev'].forEach(n => {
    const el = document.getElementById(`subtab-${n}`);
    if (el) el.style.display = n === name ? 'block' : 'none';
  });
}

// ── Health check ──────────────────────────────────────────────────────────────
async function checkHealth() {
  const dot  = document.getElementById('statusDot');
  const text = document.getElementById('statusText');
  try {
    const res = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(3000) });
    if (res.ok) {
      dot.className    = 'status-dot online';
      text.textContent = 'API online';
    } else throw new Error();
  } catch {
    dot.className    = 'status-dot offline';
    text.textContent = 'API offline — start uvicorn';
  }
}
checkHealth();
setInterval(checkHealth, 15000);


// ══════════════════════════════════════════════════════════════════════════════
// TAB 1 — Submit Policy
// ══════════════════════════════════════════════════════════════════════════════

function loadSubmitSample() {
  document.getElementById('sClauses').value = SUBMIT_SAMPLE_CLAUSES;
}

// ── PDF upload ─────────────────────────────────────────────────────────────────
function handlePdfDrop(e) {
  e.preventDefault();
  const file = e.dataTransfer.files[0];
  if (file) uploadPdf(file);
}
function handlePdfSelect(e) {
  const file = e.target.files[0];
  if (file) uploadPdf(file);
}

async function uploadPdf(file) {
  const status = document.getElementById('pdfStatus');
  status.textContent = `Uploading ${file.name}…`;
  const form = new FormData();
  form.append('file', file);
  try {
    const res  = await fetch(`${API_BASE}/upload-pdf`, { method: 'POST', body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Upload failed');
    document.getElementById('sClauses').value = data.clauses.join('\n');
    status.textContent = `✓ Extracted ${data.clause_count} clauses from ${file.name}`;
    status.style.color = '#38a169';
  } catch (err) {
    status.textContent = `✗ ${err.message}`;
    status.style.color = '#e53e3e';
  }
}

// ── Submit policy ─────────────────────────────────────────────────────────────
async function submitPolicy() {
  const rawClauses = document.getElementById('sClauses').value.trim();
  if (!rawClauses) { alert('Please enter at least one clause.'); return; }

  const clauses = rawClauses.split('\n').map(c => c.trim()).filter(Boolean);

  const payload = {
    clauses,
    submitted_by: document.getElementById('sSubmitter').value || 'underwriter',
    meta: {
      policy_type:        document.getElementById('sPolicyType').value,
      state:              document.getElementById('sState').value,
      coverage_amount:    parseInt(document.getElementById('sCoverage').value)    || 1000000,
      deductible_amount:  parseInt(document.getElementById('sDeductible').value)  || 1000,
      applicant_age:      parseInt(document.getElementById('sAge').value)         || 40,
      prior_claims_count: parseInt(document.getElementById('sClaims').value)      || 0,
    },
  };

  const btn = document.getElementById('submitBtn');
  btn.disabled = true;
  btn.textContent = 'Assessing…';

  document.getElementById('submitResult').style.display  = 'block';
  document.getElementById('submitLoading').style.display = 'block';
  document.getElementById('submitSummaryCard').style.display = 'none';
  document.getElementById('submitLoadingText').textContent =
    `Scoring ${clauses.length} clause(s) and applying ${payload.meta.state} regulations…`;

  try {
    const res  = await fetch(`${API_BASE}/submit-policy`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    _currentSubmissionId = data.id;
    renderSubmission(data);
  } catch (err) {
    alert(`Submission error: ${err.message}`);
  } finally {
    document.getElementById('submitLoading').style.display = 'none';
    btn.disabled    = false;
    btn.textContent = '⚡ Submit for AI Assessment';
  }
}

function renderSubmission(data) {
  // Workflow state banner
  const banner     = document.getElementById('wfStateBanner');
  const stateClass = { SUBMITTED: 'state-submitted', AI_ASSESSED: 'state-assessed',
                       LEGAL_REVIEW: 'state-legal', UW_REVIEW: 'state-uw',
                       APPROVED: 'state-approved', REJECTED: 'state-rejected' };
  banner.className = `workflow-state-banner ${stateClass[data.workflow_state] || ''}`;
  banner.innerHTML = `
    <strong>Submission #${data.id}</strong> &nbsp;|&nbsp;
    <span class="state-chip ${stateClass[data.workflow_state]}">${data.workflow_state.replace('_', ' ')}</span>
    &nbsp;|&nbsp; ${data.policy_type.toUpperCase()} &nbsp;|&nbsp; ${data.state}
  `;

  // Risk summary
  const s = data.risk_summary;
  document.getElementById('submitSummaryStats').innerHTML = `
    <div class="summary-box"><div class="s-val">${data.clauses.length}</div><div class="s-lbl">Total Clauses</div></div>
    <div class="summary-box"><div class="s-val">${s.Low||0}</div><div class="s-lbl">Low Risk</div></div>
    <div class="summary-box"><div class="s-val">${s.Medium||0}</div><div class="s-lbl">Medium Risk</div></div>
    <div class="summary-box flagged"><div class="s-val">${s.High||0}</div><div class="s-lbl">High Risk</div></div>
    <div class="summary-box state-flags-box"><div class="s-val">${data.state_flag_count}</div><div class="s-lbl">State Flags</div></div>
  `;

  // State flags alert
  const allStateFlags = data.clauses.flatMap(c => c.state_flags || []);
  const alertDiv      = document.getElementById('stateFlagsAlert');
  if (allStateFlags.length > 0) {
    const unique = [...new Set(allStateFlags.map(f => f.flag_id))];
    alertDiv.innerHTML = `
      <strong>⚠ State Regulation Issues (${data.state})</strong><br/>
      ${unique.map(fid => {
        const f = allStateFlags.find(x => x.flag_id === fid);
        return `<span class="state-flag-chip severity-${f?.severity?.toLowerCase()}">${fid}</span> ${f?.description || ''}`;
      }).join('<br/>')}
    `;
    alertDiv.style.display = 'block';
  } else {
    alertDiv.style.display = 'none';
  }

  // Clean policy
  document.getElementById('submitCleanText').textContent = data.generated_policy;

  // Clause diff
  document.getElementById('submitDiffList').innerHTML = data.clauses.map((c, i) => `
    <div class="clause-diff-item">
      <div class="clause-diff-header">
        <span>Clause ${c.index + 1}</span>
        <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
          ${(c.state_flags||[]).map(f =>
            `<span class="state-flag-chip severity-${f.severity?.toLowerCase()}">${f.flag_id}</span>`
          ).join('')}
          ${(c.flags||[]).map(f =>
            `<span class="flag-chip">${FLAG_LABELS[f]||f}</span>`
          ).join('')}
          <span class="badge ${c.risk_label.toLowerCase()}">${c.risk_label}</span>
          ${c.was_rewritten ? '<span class="rewrite-badge">✎ Rewritten</span>' : ''}
        </div>
      </div>
      <div class="clause-diff-body">
        ${c.was_rewritten
          ? `<div class="diff-old-text">${escHtml(c.original_text)}</div>
             <div class="diff-new-text-inline">${escHtml(c.final_text)}</div>`
          : `<div class="clause-unchanged">${escHtml(c.original_text)}</div>`}
      </div>
    </div>
  `).join('');

  // Show UW action bar if in UW_REVIEW
  const actionBar = document.getElementById('submitActionBar');
  if (data.workflow_state === 'UW_REVIEW') {
    const highCount = s.High || 0;
    document.getElementById('submitActionLabel').textContent =
      highCount === 0
        ? 'All clauses are clear. Review the generated policy and approve or request changes.'
        : `${highCount} high-risk clause(s) were rewritten by AI. Review carefully before approving.`;
    actionBar.style.display = 'flex';
  } else if (data.workflow_state === 'LEGAL_REVIEW') {
    actionBar.style.display  = 'none';
    const decBox = document.getElementById('submitDecisionBox');
    decBox.className         = 'decision-box decision-legal';
    decBox.textContent       = '⚖ This submission has been routed to the Legal Review queue due to state-specific regulatory flags. Legal team must sign off before underwriting.';
    decBox.style.display     = 'block';
  } else {
    actionBar.style.display = 'none';
  }

  document.getElementById('submitSummaryCard').style.display = 'block';
  document.getElementById('submitDecisionBox').style.display = 'none';
  showSubmitView('clean');
}

function showSubmitView(view) {
  document.getElementById('submitViewClean').style.display = view === 'clean' ? 'block' : 'none';
  document.getElementById('submitViewDiff').style.display  = view === 'diff'  ? 'block' : 'none';
  document.getElementById('btnCleanView').classList.toggle('active', view === 'clean');
  document.getElementById('btnDiffView').classList.toggle('active', view === 'diff');
}

async function finalizeSubmission(decision) {
  if (!_currentSubmissionId) return;

  const note = decision === 'REJECTED'
    ? (prompt('Reason for requesting changes (optional):') || '')
    : '';

  try {
    const res = await fetch(`${API_BASE}/submissions/${_currentSubmissionId}/finalize`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ decision, note, actor: document.getElementById('sSubmitter').value || 'underwriter' }),
    });
    if (!res.ok) throw new Error(await res.text());

    const box = document.getElementById('submitDecisionBox');
    const ts  = new Date().toLocaleString();
    if (decision === 'APPROVED') {
      box.className   = 'decision-box approved';
      box.textContent = `✓ Policy APPROVED at ${ts}. Proceeding to issuance.`;
    } else {
      box.className   = 'decision-box rejected';
      box.textContent = `✗ Changes requested at ${ts}. Policy returned for revision.${note ? ' Note: ' + note : ''}`;
    }
    box.style.display = 'block';
    document.getElementById('submitActionBar').style.display = 'none';
    box.scrollIntoView({ behavior: 'smooth', block: 'center' });
    loadQueues();   // refresh queue badges
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}


// ══════════════════════════════════════════════════════════════════════════════
// TAB 2 — Review Queues
// ══════════════════════════════════════════════════════════════════════════════

async function loadQueues() {
  await Promise.all([loadUwQueue(), loadLegalQueue()]);
  updateQueueBadge();
}

async function loadUwQueue() {
  try {
    const res  = await fetch(`${API_BASE}/queue/uw`);
    const data = await res.json();
    document.getElementById('uwQueueCount').textContent = data.length;
    renderQueueTable('uwQueueBody', data, 'uw');
  } catch { /* ignore */ }
}

async function loadLegalQueue() {
  try {
    const res  = await fetch(`${API_BASE}/queue/legal`);
    const data = await res.json();
    document.getElementById('legalQueueCount').textContent = data.length;
    renderQueueTable('legalQueueBody', data, 'legal');
  } catch { /* ignore */ }
}

function updateQueueBadge() {
  const uw     = parseInt(document.getElementById('uwQueueCount').textContent)    || 0;
  const legal  = parseInt(document.getElementById('legalQueueCount').textContent) || 0;
  const total  = uw + legal;
  const badge  = document.getElementById('queueBadge');
  badge.textContent = total;
  badge.style.display = total > 0 ? 'inline-flex' : 'none';
}

function renderQueueTable(tbodyId, items, type) {
  const tbody = document.getElementById(tbodyId);
  if (!items.length) {
    tbody.innerHTML = `<tr><td colspan="10" class="empty-row">Queue is empty.</td></tr>`;
    return;
  }
  tbody.innerHTML = items.map(item => {
    const s = item.risk_summary || {};
    if (type === 'uw') {
      return `<tr>
        <td>#${item.id}</td>
        <td>${item.policy_type}</td>
        <td>${item.state}</td>
        <td><span class="badge low">${s.Low||0}</span></td>
        <td><span class="badge medium">${s.Medium||0}</span></td>
        <td><span class="badge high">${s.High||0}</span></td>
        <td>${item.state_flag_count > 0 ? `<span class="state-flag-chip severity-medium">${item.state_flag_count}</span>` : '—'}</td>
        <td style="font-size:12px">${fmtDate(item.submitted_at)}</td>
        <td style="font-size:12px">${item.submitted_by}</td>
        <td><button class="btn-mini" onclick="openReviewModal(${item.id}, 'uw')">Review →</button></td>
      </tr>`;
    } else {
      return `<tr>
        <td>#${item.id}</td>
        <td>${item.policy_type}</td>
        <td>${item.state}</td>
        <td><span class="badge high">${s.High||0}</span></td>
        <td>${item.state_flag_count > 0 ? `<span class="state-flag-chip severity-high">${item.state_flag_count}</span>` : '—'}</td>
        <td style="font-size:12px">${fmtDate(item.submitted_at)}</td>
        <td style="font-size:12px">${item.submitted_by}</td>
        <td><button class="btn-mini btn-mini-legal" onclick="openReviewModal(${item.id}, 'legal')">Review →</button></td>
      </tr>`;
    }
  }).join('');
}

async function openReviewModal(submissionId, type) {
  document.getElementById('reviewModal').style.display = 'flex';
  document.getElementById('modalTitle').textContent    = `Reviewing Submission #${submissionId}`;
  document.getElementById('modalBody').innerHTML       = '<div style="text-align:center;padding:40px"><div class="spinner" style="margin:auto"></div><p>Loading…</p></div>';
  document.getElementById('modalFooter').innerHTML     = '';

  try {
    const res  = await fetch(`${API_BASE}/submissions/${submissionId}`);
    const data = await res.json();
    renderModalBody(data, type);
    renderModalFooter(data, type);
  } catch (err) {
    document.getElementById('modalBody').innerHTML = `<p style="color:red">Error: ${err.message}</p>`;
  }
}

function renderModalBody(data, type) {
  const allStateFlags = data.clauses.flatMap(c => c.state_flags || []);
  const stateAlertHtml = allStateFlags.length > 0 ? `
    <div class="state-alert" style="margin-bottom:16px">
      <strong>⚠ State Regulation Flags (${data.state})</strong><br/>
      ${[...new Set(allStateFlags.map(f => f.flag_id))].map(fid => {
        const f = allStateFlags.find(x => x.flag_id === fid);
        return `<span class="state-flag-chip severity-${f?.severity?.toLowerCase()}">${fid}</span> ${f?.description||''}`;
      }).join('<br/>')}
    </div>` : '';

  const clausesHtml = data.clauses.map(c => `
    <div class="clause-diff-item">
      <div class="clause-diff-header">
        <span>Clause ${c.index + 1}</span>
        <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
          ${(c.state_flags||[]).map(f =>
            `<span class="state-flag-chip severity-${f.severity?.toLowerCase()}">${f.flag_id}</span>`
          ).join('')}
          <span class="badge ${c.risk_label.toLowerCase()}">${c.risk_label}</span>
          ${c.was_rewritten ? '<span class="rewrite-badge">✎ Rewritten</span>' : ''}
          <span class="decision-chip ${c.decision.toLowerCase()}">${c.decision}</span>
        </div>
      </div>
      <div class="clause-diff-body">
        ${c.was_rewritten
          ? `<div class="diff-old-text">${escHtml(c.original_text)}</div>
             <div class="diff-new-text-inline">${escHtml(c.final_text)}</div>`
          : `<div class="clause-unchanged">${escHtml(c.original_text)}</div>`}
      </div>
    </div>
  `).join('');

  document.getElementById('modalBody').innerHTML = stateAlertHtml + clausesHtml;
}

function renderModalFooter(data, type) {
  const footer = document.getElementById('modalFooter');
  if (type === 'legal') {
    footer.innerHTML = `
      <input type="text" id="legalNote" placeholder="Legal review note (optional)" style="flex:1" />
      <button class="btn-approve" onclick="legalApprove(${data.id})">✓ Clear for UW Review</button>
      <button class="btn-reject"  onclick="closeModal()">Cancel</button>
    `;
  } else {
    footer.innerHTML = `
      <input type="text" id="uwNote" placeholder="Underwriter note (optional)" style="flex:1" />
      <button class="btn-approve" onclick="modalFinalize(${data.id},'APPROVED')">✓ Approve</button>
      <button class="btn-reject"  onclick="modalFinalize(${data.id},'REJECTED')">✗ Reject</button>
    `;
  }
}

async function legalApprove(submissionId) {
  const note = document.getElementById('legalNote')?.value || '';
  try {
    const res = await fetch(
      `${API_BASE}/submissions/${submissionId}/legal-approve?note=${encodeURIComponent(note)}&actor=legal_team`,
      { method: 'POST' }
    );
    if (!res.ok) throw new Error(await res.text());
    closeModal();
    loadQueues();
  } catch (err) { alert(`Error: ${err.message}`); }
}

async function modalFinalize(submissionId, decision) {
  const note = document.getElementById('uwNote')?.value || '';
  try {
    const res = await fetch(`${API_BASE}/submissions/${submissionId}/finalize`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ decision, note, actor: 'underwriter' }),
    });
    if (!res.ok) throw new Error(await res.text());
    closeModal();
    loadQueues();
  } catch (err) { alert(`Error: ${err.message}`); }
}

function closeModal() {
  document.getElementById('reviewModal').style.display = 'none';
}


// ══════════════════════════════════════════════════════════════════════════════
// TAB 3 — Audit Log
// ══════════════════════════════════════════════════════════════════════════════

async function loadAuditLog() {
  try {
    const res    = await fetch(`${API_BASE}/audit-log?limit=100`);
    const events = await res.json();
    const tbody  = document.getElementById('auditBody');
    if (!events.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="empty-row">No audit events yet.</td></tr>';
      return;
    }
    tbody.innerHTML = events.map(e => `
      <tr>
        <td style="font-size:12px;white-space:nowrap">${fmtDate(e.created_at)}</td>
        <td><a href="#" onclick="viewSubmission(${e.submission_id});return false">#${e.submission_id}</a></td>
        <td><span class="event-chip ${e.event_type.toLowerCase()}">${e.event_type}</span></td>
        <td style="font-size:12px">${e.actor}</td>
        <td style="font-size:12px">${e.description}</td>
      </tr>
    `).join('');
  } catch (err) { console.error('Audit log error:', err); }
}

async function viewSubmission(id) {
  // Switch to queue tab and open modal
  document.querySelectorAll('.nav-tab').forEach(t => {
    if (t.dataset.tab === 'queue') t.click();
  });
  setTimeout(() => openReviewModal(id, 'uw'), 100);
}


// ══════════════════════════════════════════════════════════════════════════════
// TAB 4 — Dev Tools (single clause + bulk)
// ══════════════════════════════════════════════════════════════════════════════

function loadSample(level) {
  document.getElementById('clauseInput').value = SAMPLES[level];
}

function buildPayload() {
  return {
    clause:             document.getElementById('clauseInput').value.trim(),
    policy_type:        document.getElementById('policyType').value,
    coverage_amount:    parseInt(document.getElementById('coverageAmount').value) || 100000,
    applicant_age:      parseInt(document.getElementById('applicantAge').value)   || 35,
    prior_claims_count: parseInt(document.getElementById('priorClaims').value)    || 0,
    deductible_amount:  parseInt(document.getElementById('deductible').value)     || 1000,
    state:              document.getElementById('stateCode').value,
  };
}

async function assessClause() {
  const payload = buildPayload();
  if (!payload.clause) { alert('Please enter a policy clause.'); return; }

  const btn = document.getElementById('assessBtn');
  btn.disabled = true;
  btn.textContent = 'Assessing…';
  document.getElementById('resultCard').style.display  = 'none';
  document.getElementById('loadingCard').style.display = 'block';

  try {
    const res  = await fetch(`${API_BASE}/assess-risk`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(await res.text());
    renderResult(await res.json(), payload.clause);
  } catch (err) {
    alert(`Error: ${err.message}`);
  } finally {
    document.getElementById('loadingCard').style.display = 'none';
    btn.disabled    = false;
    btn.textContent = 'Assess Risk';
  }
}

function renderResult(data, originalClause) {
  const level = data.risk_label.toLowerCase();

  document.getElementById('riskBanner').className = `risk-banner ${level}`;
  document.getElementById('riskLabel').textContent = data.risk_label.toUpperCase();

  const pct = Math.round(data.confidence * 100);
  document.getElementById('confBar').style.width      = `${pct}%`;
  document.getElementById('confPct').textContent      = `${pct}%`;
  document.getElementById('confBar').style.background =
    level === 'high' ? '#e53e3e' : level === 'medium' ? '#ed8936' : '#38a169';

  const grid = document.getElementById('probGrid');
  grid.innerHTML = '';
  Object.entries(data.lr_probabilities).forEach(([cls, prob]) => {
    const div = document.createElement('div');
    div.className = `prob-item ${data.risk_label === cls ? 'active' : ''}`;
    div.innerHTML = `<div class="prob-cls">${cls}</div><div class="prob-val">${Math.round(prob*100)}%</div>`;
    grid.appendChild(div);
  });

  // State flags
  const sfSection = document.getElementById('stateFlagsSection');
  const sfList    = document.getElementById('stateFlagsList');
  if (data.state_flags?.length > 0) {
    sfList.innerHTML = data.state_flags.map(f =>
      `<div class="state-flag-row">
        <span class="state-flag-chip severity-${f.severity?.toLowerCase()}">${f.flag_id}</span>
        <span style="font-size:13px">${f.description}</span>
      </div>`
    ).join('');
    sfSection.style.display = 'block';
  } else {
    sfSection.style.display = 'none';
  }

  // Risk flags
  const flagsSection = document.getElementById('flagsSection');
  if (data.flags?.length > 0) {
    document.getElementById('flagsList').innerHTML = data.flags
      .map(f => `<span class="flag-chip ${level === 'medium' ? 'medium' : ''}">${FLAG_LABELS[f]||f}</span>`)
      .join('');
    flagsSection.style.display = 'block';
  } else {
    flagsSection.style.display = 'none';
  }

  // Rewrite
  const rwSection = document.getElementById('rewriteSection');
  if (data.rewritten_clause) {
    document.getElementById('origText').textContent = originalClause;
    document.getElementById('newText').textContent  = data.rewritten_clause;
    rwSection.style.display = 'block';
  } else {
    rwSection.style.display = 'none';
  }

  document.getElementById('resultCard').style.display = 'block';
}

// ── Bulk CSV ───────────────────────────────────────────────────────────────────
function handleDrop(event) {
  event.preventDefault();
  const file = event.dataTransfer.files[0];
  if (file) processCsvFile(file);
}
function handleFileSelect(event) {
  const file = event.target.files[0];
  if (file) processCsvFile(file);
}

async function processCsvFile(file) {
  const text = await file.text();
  const rows = parseCsv(text);
  if (!rows.length) { alert('No rows found in CSV.'); return; }

  const progress     = document.getElementById('bulkProgress');
  const progressBar  = document.getElementById('progressBar');
  const progressText = document.getElementById('progressText');
  progress.style.display = 'block';
  _bulkData = [];

  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    if (!row.clause) continue;
    const payload = {
      clause:             row.clause,
      policy_type:        row.policy_type        || 'auto',
      coverage_amount:    parseInt(row.coverage_amount)    || 100000,
      applicant_age:      parseInt(row.applicant_age)      || 35,
      prior_claims_count: parseInt(row.prior_claims_count) || 0,
      deductible_amount:  parseInt(row.deductible_amount)  || 1000,
      state:              row.state || 'TX',
    };
    try {
      const res    = await fetch(`${API_BASE}/assess-risk`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const result = await res.json();
      _bulkData.push({ ...payload, ...result });
    } catch {
      _bulkData.push({ ...payload, risk_label: 'Error', confidence: 0, flags: [], state_flags: [] });
    }
    progressBar.style.width  = `${Math.round(((i+1)/rows.length)*100)}%`;
    progressText.textContent = `Processing ${i+1} / ${rows.length}…`;
  }

  progress.style.display = 'none';
  renderBulkResults(_bulkData);
}

function parseCsv(text) {
  const lines   = text.trim().split('\n');
  const headers = lines[0].split(',').map(h => h.trim().replace(/"/g, ''));
  return lines.slice(1).map(line => {
    const vals = line.split(',').map(v => v.trim().replace(/"/g, ''));
    return Object.fromEntries(headers.map((h, i) => [h, vals[i] || '']));
  });
}

function renderBulkResults(data) {
  const total   = data.length;
  const flagged = data.filter(r => r.risk_label === 'High').length;
  const medium  = data.filter(r => r.risk_label === 'Medium').length;
  const low     = data.filter(r => r.risk_label === 'Low').length;

  document.getElementById('bulkSummary').innerHTML = `
    <div class="summary-box"><div class="s-val">${total}</div><div class="s-lbl">Total</div></div>
    <div class="summary-box"><div class="s-val">${low}</div><div class="s-lbl">Low</div></div>
    <div class="summary-box"><div class="s-val">${medium}</div><div class="s-lbl">Medium</div></div>
    <div class="summary-box flagged"><div class="s-val">${flagged}</div><div class="s-lbl">High</div></div>
  `;

  document.getElementById('resultsBody').innerHTML = data.map(r => `
    <tr>
      <td style="max-width:300px">${(r.clause||'').substring(0,100)}${(r.clause||'').length>100?'…':''}</td>
      <td><span class="badge ${r.risk_label?.toLowerCase()}">${r.risk_label}</span></td>
      <td>${r.confidence ? Math.round(r.confidence*100)+'%' : '—'}</td>
      <td style="font-size:11px">${(r.flags||[]).map(f=>FLAG_LABELS[f]||f).join(', ')||'—'}</td>
    </tr>
  `).join('');

  document.getElementById('bulkResults').style.display = 'block';
}

function downloadResults() {
  if (!_bulkData.length) return;
  const headers = ['clause','risk_label','confidence','flags','rewritten_clause'];
  const csv = [
    headers.join(','),
    ..._bulkData.map(r => headers.map(h => {
      const val = h === 'flags' ? (r.flags||[]).join('|') : (r[h]??'');
      return `"${String(val).replace(/"/g,'""')}"`;
    }).join(','))
  ].join('\n');
  const a    = document.createElement('a');
  a.href     = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
  a.download = 'risk_assessment_results.csv';
  a.click();
}

// ── Utilities ─────────────────────────────────────────────────────────────────
function fmtDate(iso) {
  return new Date(iso).toLocaleString('en-US', { dateStyle:'short', timeStyle:'short' });
}

function escHtml(str) {
  return (str||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

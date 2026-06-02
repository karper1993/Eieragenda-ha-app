const EIERAGENDA_BASE = (window.EIERAGENDA_BASE || '').replace(/\/$/, '');
function appPath(path) {
  if (!path.startsWith('/')) path = '/' + path;
  return EIERAGENDA_BASE + path;
}

function openOrderModal() {
  resetOrderForm();
  openModal('orderModal');
}
function openModal(id) {
  document.getElementById(id).classList.add('open');
  document.body.classList.add('modal-open');
  if (!history.state || history.state.modal !== id) {
    history.pushState({ modal: id }, '', window.location.href);
  }
}
function closeModal(id, fromPopstate = false) {
  document.getElementById(id).classList.remove('open');
  document.body.classList.remove('modal-open');
  if (!fromPopstate && history.state && history.state.modal === id) {
    history.back();
  }
}
function showMessage(text) {
  const old = document.querySelector('.toast');
  if (old) old.remove();
  const div = document.createElement('div');
  div.className = 'toast';
  div.textContent = text;
  document.body.appendChild(div);
  setTimeout(() => div.remove(), 3300);
}
function toggleNewCustomer(select) {
  const form = select.closest('form');
  const block = form.querySelector('.new-customer-block');
  if (!block) return;
  block.classList.toggle('hidden', select.value !== '__new__');
}
function updateTimeMode(form) {
  const mode = form.querySelector('input[name="tijd_type"]:checked')?.value || 'none';
  form.querySelectorAll('.time-panel').forEach(p => p.classList.add('hidden'));
  const panel = form.querySelector('.time-' + mode);
  if (panel) panel.classList.remove('hidden');
}
function normalizeTimeFields(form) {
  const mode = form.querySelector('input[name="tijd_type"]:checked')?.value || 'none';
  const exact = form.querySelector('input[name="tijd_van"]');
  const rangeFrom = form.querySelector('input[name="tijd_van_range"]');
  const rangeTo = form.querySelector('input[name="tijd_tot_range"]');
  let hiddenTo = form.querySelector('input[name="tijd_tot"]');
  if (!hiddenTo) {
    hiddenTo = document.createElement('input');
    hiddenTo.type = 'hidden';
    hiddenTo.name = 'tijd_tot';
    form.appendChild(hiddenTo);
  }
  if (mode === 'range') {
    if (exact && rangeFrom) exact.value = rangeFrom.value;
    hiddenTo.value = rangeTo ? rangeTo.value : '';
  } else {
    hiddenTo.value = '';
  }
}
function validateOrderForm(form) {
  normalizeTimeFields(form);
  const klant = form.querySelector('select[name="klant_id"]')?.value || '';
  const nieuweKlant = form.querySelector('input[name="nieuwe_klant"]')?.value.trim() || '';
  const s1 = Number(form.querySelector('input[name="soort1"]')?.value || 0);
  const s2 = Number(form.querySelector('input[name="soort2"]')?.value || 0);
  const dd = Number(form.querySelector('input[name="dubbeldooiers"]')?.value || 0);
  if (!klant) {
    showMessage('Niet alle velden ingevuld: selecteer een klant of kies + Nieuwe klant.');
    return false;
  }
  if (klant === '__new__' && !nieuweKlant) {
    showMessage('Niet alle velden ingevuld: vul de naam van de nieuwe klant in.');
    return false;
  }
  if ((s1 + s2 + dd) <= 0) {
    showMessage('Niet alle velden ingevuld: vul minimaal één aantal eieren in.');
    return false;
  }
  return true;
}
function selectAllOnFocus(input) { setTimeout(() => input.select(), 0); }
function stackText(amount) {
  amount = Number(amount || 0);
  if (amount < 30) return amount > 0 ? amount + ' eieren' : '';

  const stapels = Math.floor(amount / 180);
  let rest = amount % 180;
  const bladen = Math.floor(rest / 30);
  const eieren = rest % 30;

  const parts = [];
  if (stapels) parts.push(stapels + (stapels === 1 ? ' stapel' : ' stapels'));
  if (bladen) parts.push(bladen + (bladen === 1 ? ' blad' : ' bladen'));
  if (eieren) parts.push(eieren + ' eieren');

  return parts.join(' en ');
}
function updateStacks(input) {
  const label = input.closest('.form-block')?.querySelector('.stack-label');
  if (label) label.textContent = stackText(input.value);
}
async function toggleProcessed(id, btn) {
  const res = await fetch(appPath(`/bestelling/${id}/toggle/verwerkt`), { method: 'POST' });
  if (!res.ok) return;
  const data = await res.json();
  btn.classList.toggle('on', !!data.waarde);
  const created = btn.querySelector('small')?.textContent?.startsWith('Aangemaakt:') ? btn.querySelector('small').outerHTML : '';
  if (data.waarde) {
    const now = new Date();
    const hh = String(now.getHours()).padStart(2, '0');
    const mm = String(now.getMinutes()).padStart(2, '0');
    btn.innerHTML = created + '<span>Voltooid ✓</span><small>Voltooid: vandaag ' + hh + ':' + mm + '</small>';
  } else {
    btn.innerHTML = created + '<span>Voltooien</span>';
  }
  const card = btn.closest('.order-card');
  if (card) card.classList.toggle('completed', !!data.waarde);
  setTimeout(() => window.location.reload(), 350);
}
document.addEventListener('DOMContentLoaded', () => {
  if (window.initialError) showMessage(window.initialError);
  document.querySelectorAll('select[name="klant_id"]').forEach(toggleNewCustomer);
  document.querySelectorAll('.order-form').forEach(updateTimeMode);
  document.querySelectorAll('.egg-input').forEach(updateStacks);
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    document.querySelectorAll('.modal.open').forEach(m => m.classList.remove('open'));
    document.body.classList.remove('modal-open');
  }
});
let currentToken = document.body.dataset.stateToken || '';
setInterval(async () => {
  const active = document.activeElement;
  const isTyping = active && ['INPUT', 'TEXTAREA', 'SELECT'].includes(active.tagName);
  const modalOpen = document.querySelector('.modal.open');
  if (isTyping || modalOpen || !currentToken) return;
  try {
    const res = await fetch(appPath('/api/state'), { cache: 'no-store' });
    const data = await res.json();
    if (data.token && data.token !== currentToken) window.location.reload();
  } catch (e) {}
}, 4000);


window.addEventListener('popstate', () => {
  const open = document.querySelector('.modal.open');
  if (open) {
    closeModal(open.id, true);
  }
});

function showCustomerDetails(name, phone, address, email) {
  const nameEl = document.getElementById('customerModalName');
  const phoneEl = document.getElementById('customerModalPhone');
  const addressEl = document.getElementById('customerModalAddress');
  const emailEl = document.getElementById('customerModalEmail');
  if (!nameEl || !phoneEl || !addressEl) return;

  nameEl.textContent = name || 'Klant';
  addressEl.innerHTML = address ? '<strong>Adres:</strong> ' + address : '<strong>Adres:</strong> niet ingevuld';
  if (emailEl) {
    emailEl.innerHTML = email ? '<strong>Mailadres:</strong> <a href="mailto:' + email + '">' + email + '</a>' : '<strong>Mailadres:</strong> niet ingevuld';
  }
  phoneEl.innerHTML = phone ? '<strong>Telefoon:</strong> <a href="tel:' + phone + '">' + phone + '</a>' : '<strong>Telefoon:</strong> niet ingevuld';

  openModal('customerModal');
}


function debounce(fn, wait) {
  let timeout;
  return (...args) => {
    clearTimeout(timeout);
    timeout = setTimeout(() => fn(...args), wait);
  };
}

function setupAddressAutocomplete(input) {
  const box = input.closest('.address-block')?.querySelector('.address-suggestions');
  if (!box) return;

  const search = debounce(async () => {
    const q = input.value.trim();
    if (q.length < 3) {
      box.innerHTML = '';
      box.classList.remove('open');
      return;
    }

    try {
      const url = 'https://api.pdok.nl/bzk/locatieserver/search/v3_1/suggest?fq=type:adres&rows=6&q=' + encodeURIComponent(q);
      const res = await fetch(url);
      const data = await res.json();
      const docs = data?.response?.docs || [];

      box.innerHTML = '';
      docs.forEach(doc => {
        const text = doc.weergavenaam || doc.suggest || doc.id;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.textContent = text;
        btn.addEventListener('click', () => {
          input.value = text;
          box.innerHTML = '';
          box.classList.remove('open');
        });
        box.appendChild(btn);
      });
      box.classList.toggle('open', docs.length > 0);
    } catch (e) {
      box.innerHTML = '';
      box.classList.remove('open');
    }
  }, 250);

  input.addEventListener('input', search);
  input.addEventListener('blur', () => setTimeout(() => box.classList.remove('open'), 200));
  input.addEventListener('focus', () => {
    if (box.children.length) box.classList.add('open');
  });
}

function showCustomerDetails(name, phone, address, email) {
  const nameEl = document.getElementById('customerModalName');
  const phoneEl = document.getElementById('customerModalPhone');
  const addressEl = document.getElementById('customerModalAddress');
  const emailEl = document.getElementById('customerModalEmail');
  if (!nameEl || !phoneEl || !addressEl) return;

  nameEl.textContent = name || 'Klant';
  addressEl.innerHTML = address ? '<strong>Adres:</strong> ' + address : '<strong>Adres:</strong> niet ingevuld';
  if (emailEl) {
    emailEl.innerHTML = email ? '<strong>Mailadres:</strong> <a href="mailto:' + email + '">' + email + '</a>' : '<strong>Mailadres:</strong> niet ingevuld';
  }
  phoneEl.innerHTML = phone ? '<strong>Telefoon:</strong> <a href="tel:' + phone + '">' + phone + '</a>' : '<strong>Telefoon:</strong> niet ingevuld';

  openModal('customerModal');
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.address-input').forEach(setupAddressAutocomplete);
});


let headerCompact = false;

function updateScrolledHeader() {
  if (!document.body.classList.contains('page-agenda')) {
    document.body.classList.remove('is-scrolled');
    return;
  }
  const y = window.scrollY || 0;

  // Hysterese voorkomt flikkeren/bibberen rond het omschakelpunt
  if (!headerCompact && y > 40) {
    headerCompact = true;
    document.body.classList.add('is-scrolled');
  } else if (headerCompact && y < 12) {
    headerCompact = false;
    document.body.classList.remove('is-scrolled');
  }
}

window.addEventListener('scroll', updateScrolledHeader, { passive: true });
document.addEventListener('DOMContentLoaded', updateScrolledHeader);


// outside popup close v17
document.addEventListener('click', (e) => {
  const modal = e.target.closest('.modal.open');
  if (modal && e.target === modal) {
    closeModal(modal.id);
  }
});


function filterCustomerSelect(input) {
  const form = input.closest('form');
  const select = form ? form.querySelector('select[name="klant_id"]') : null;
  if (!select) return;

  const q = input.value.trim().toLowerCase();
  let firstVisible = null;

  [...select.options].forEach((opt) => {
    if (!opt.value || opt.value === '__new__') {
      opt.hidden = false;
      return;
    }

    const hay = (opt.dataset.search || opt.textContent || '').toLowerCase();
    const show = !q || hay.includes(q);
    opt.hidden = !show;
    if (show && !firstVisible) firstVisible = opt;
  });

  if (q && firstVisible) {
    select.value = firstVisible.value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  }
}

async function updateLastOrderPreview(select) {
  const form = select.closest('form');
  const panel = form ? form.querySelector('.repeat-last-panel') : null;
  if (!panel) return;

  const summary = panel.querySelector('.repeat-last-summary');
  panel.classList.add('hidden');
  panel.dataset.order = '';

  const klantId = select.value;
  if (!klantId || klantId === '__new__') return;

  try {
    const res = await fetch('api/klant/' + klantId + '/laatste-bestelling', { cache: 'no-store' });
    const data = await res.json();
    if (!data.ok) return;

    panel.dataset.order = JSON.stringify(data);
    summary.textContent = 'Laatste: ' + data.summary + (data.datum ? ' · ' + data.datum : '');
    panel.classList.remove('hidden');
  } catch (e) {}
}

function setChecked(form, name, value) {
  const el = form.querySelector('input[name="' + name + '"]');
  if (el) el.checked = !!value;
}

function repeatLastOrder(btn) {
  const panel = btn.closest('.repeat-last-panel');
  const form = btn.closest('form');
  if (!panel || !form || !panel.dataset.order) return;

  const data = JSON.parse(panel.dataset.order);

  const s1 = form.querySelector('input[name="soort1"]');
  const s2 = form.querySelector('input[name="soort2"]');
  const dd = form.querySelector('input[name="dubbeldooiers"]');
  if (s1) { s1.value = data.soort1 || 0; updateStacks(s1); }
  if (s2) { s2.value = data.soort2 || 0; updateStacks(s2); }
  if (dd) { dd.value = data.dubbeldooiers || 0; updateStacks(dd); }

  setChecked(form, 'factuur', data.factuur);
  setChecked(form, 'factuur_meegeven', data.factuur_meegeven);
  setChecked(form, 'pinnen', data.pinnen);
  setChecked(form, 'contant', data.contant);

  const opm = form.querySelector('input[name="opmerking"], textarea[name="opmerking"]');
  if (opm) opm.value = data.opmerking || '';

  showMessage('Laatste bestelling overgenomen. Controleer en plaats daarna de bestelling.');
}

document.addEventListener('DOMContentLoaded', () => {
  const params = new URLSearchParams(window.location.search);
  const repeatCustomerId = params.get('repeat_customer_id');
  if (repeatCustomerId) {
    const modal = document.getElementById('orderModal');
    const select = document.querySelector('#orderModal select[name="klant_id"], form select[name="klant_id"]');
    if (modal && select) {
      openOrderModal();
      select.value = repeatCustomerId;
      select.dispatchEvent(new Event('change'));
      setTimeout(() => {
        const btn = document.querySelector('.repeat-last-btn');
        if (btn && !btn.closest('.repeat-last-panel')?.classList.contains('hidden')) {
          btn.click();
        }
      }, 600);
    }
  }
});

document.addEventListener('change', (e) => {
  if (e.target && e.target.matches('select[name="klant_id"]')) {
    updateLastOrderPreview(e.target);
  }
});


function resetOrderForm() {
  const modal = document.getElementById('orderModal');
  if (!modal) return;
  const form = modal.querySelector('form');
  if (!form) return;

  form.reset();

  const customerSelect = form.querySelector('select[name="klant_id"]');
  if (customerSelect) customerSelect.value = "";

  const search = form.querySelector('.customer-search-input');
  if (search) search.value = "";

  form.querySelectorAll('select[name="klant_id"] option').forEach(opt => opt.hidden = false);

  form.querySelectorAll('.egg-input').forEach(input => {
    input.value = 0;
    updateStacks(input);
  });

  form.querySelectorAll('.new-customer-block').forEach(block => block.classList.add('hidden'));
  form.querySelectorAll('.time-panel').forEach(panel => panel.classList.add('hidden'));

  const none = form.querySelector('input[name="tijd_type"][value="none"]');
  if (none) none.checked = true;

  const repeatPanel = form.querySelector('.repeat-last-panel');
  if (repeatPanel) {
    repeatPanel.classList.add('hidden');
    repeatPanel.dataset.order = '';
    const summary = repeatPanel.querySelector('.repeat-last-summary');
    if (summary) summary.textContent = '';
  }

  form.querySelectorAll('.payment-grid input[type="checkbox"], .invoice-toggle input[type="checkbox"]').forEach(chk => {
    chk.checked = false;
  });

  updateTimeMode(form);
}


// repeat_customer_id_safe_v47
document.addEventListener('DOMContentLoaded', () => {
  const params = new URLSearchParams(window.location.search);
  const repeatCustomerId = params.get('repeat_customer_id');
  if (!repeatCustomerId) return;

  const modal = document.getElementById('orderModal');
  const select = document.querySelector('#orderModal select[name="klant_id"]');
  if (!modal || !select) return;

  openOrderModal();
  select.value = repeatCustomerId;
  select.dispatchEvent(new Event('change', { bubbles: true }));

  setTimeout(() => {
    const btn = document.querySelector('#orderModal .repeat-last-btn');
    const panel = document.querySelector('#orderModal .repeat-last-panel');
    if (btn && panel && !panel.classList.contains('hidden')) {
      btn.click();
    }
  }, 800);
});

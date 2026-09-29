(() => {
  document.querySelectorAll('[data-evidence-form]').forEach(form => {
    const checks = [...form.querySelectorAll('input[name="evidence_item_ids"]')];
    const count = form.querySelector('[data-evidence-count]');
    const summary = form.querySelector('[data-evidence-summary]');
    const filter = form.querySelector('[data-evidence-filter]');
    const empty = form.querySelector('[data-evidence-empty]');
    const update = () => { const n = checks.filter(x => x.checked).length; if (count) count.textContent = `${n} selected`; if (summary) summary.classList.toggle('has-selection', n > 0); };
    const visible = () => [...form.querySelectorAll('[data-evidence-search]')].filter(el => !el.hidden && !el.closest('[data-evidence-group]')?.hidden);
    checks.forEach(x => x.addEventListener('change', update));
    filter?.addEventListener('input', () => { const q = filter.value.trim().toLowerCase(); let matches = 0; form.querySelectorAll('[data-evidence-group]').forEach(group => { let gm = 0; group.querySelectorAll('[data-evidence-search]').forEach(el => { const hit = !q || el.dataset.evidenceSearch.includes(q); el.hidden = !hit; if (hit) { matches++; gm++; } }); if (q && gm) group.open = true; group.hidden = Boolean(q && !gm); }); if (empty) empty.hidden = matches !== 0; });
    form.querySelector('[data-evidence-select-visible]')?.addEventListener('click', () => { visible().forEach(el => { const cb = el.querySelector('input[type="checkbox"]'); if (cb) cb.checked = true; }); update(); });
    form.querySelector('[data-evidence-clear]')?.addEventListener('click', () => { checks.forEach(cb => cb.checked = false); update(); });
    update();
  });
})();

(() => {
  const shell = document.querySelector('[data-app-shell]');
  if (!shell) return;

  const context = document.querySelector('[data-context-nav]');
  const backdrop = document.querySelector('[data-mobile-backdrop]');
  const storageKey = 'njs-context-nav-collapsed';
  const contextArea = context?.dataset.contextArea || document.querySelector('.rail-item.is-active[data-area]')?.dataset.area || 'default';

  // v3.3.2: keep second/third navigation anchored across full-page loads.
  // This prevents long Campaign/Content menus from jumping back to their first item.
  const persistentScrollRegions = [];
  if (context) persistentScrollRegions.push({el: context.querySelector('.context-actions'), key: `njs-nav-scroll:${contextArea}`});
  document.querySelectorAll('[data-persist-scroll]').forEach((el, index) => persistentScrollRegions.push({el, key: `njs-persist-scroll:${el.dataset.persistScroll || index}`}));
  persistentScrollRegions.forEach(({el,key}) => {
    if (!el) return;
    const saved = Number(localStorage.getItem(key));
    if (Number.isFinite(saved) && saved > 0) requestAnimationFrame(() => { el.scrollTop = saved; });
    else {
      const active = el.querySelector('.is-active');
      if (active) requestAnimationFrame(() => active.scrollIntoView({block:'nearest', inline:'nearest'}));
    }
    let scrollTimer;
    el.addEventListener('scroll', () => { clearTimeout(scrollTimer); scrollTimer = setTimeout(() => localStorage.setItem(key, String(el.scrollTop)), 40); }, {passive:true});
    el.querySelectorAll('a[href]').forEach(link => link.addEventListener('click', () => localStorage.setItem(key, String(el.scrollTop))));
  });

  function setCollapsed(value, persist = true) {
    shell.classList.toggle('context-collapsed', value);
    document.querySelectorAll('[data-context-toggle]').forEach(button => button.setAttribute('aria-expanded', value ? 'false' : 'true'));
    if (persist) localStorage.setItem(storageKey, value ? '1' : '0');
  }

  if (window.innerWidth > 980 && localStorage.getItem(storageKey) === '1') setCollapsed(true, false);

  document.querySelectorAll('[data-context-toggle]').forEach(button => button.addEventListener('click', () => {
    if (window.innerWidth <= 980) shell.classList.toggle('mobile-nav-open');
    else setCollapsed(!shell.classList.contains('context-collapsed'));
  }));

  document.querySelector('[data-mobile-nav]')?.addEventListener('click', () => shell.classList.toggle('mobile-nav-open'));
  backdrop?.addEventListener('click', () => shell.classList.remove('mobile-nav-open'));

  document.querySelectorAll('.rail-item[data-area]').forEach(item => item.addEventListener('click', () => {
    if (window.innerWidth > 980) setCollapsed(false);
  }));

  document.querySelectorAll('[data-dismiss-flash]').forEach(button => button.addEventListener('click', () => button.closest('.flash')?.remove()));

  const quickCreate = document.querySelector('[data-quick-create]');
  document.addEventListener('click', event => {
    if (quickCreate?.open && !quickCreate.contains(event.target)) quickCreate.open = false;
  });

  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      shell.classList.remove('mobile-nav-open');
      if (quickCreate) quickCreate.open = false;
    }
  });

  // v2.0: consistent pending feedback for actions that navigate/queue work.
  document.addEventListener('submit', event => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || form.dataset.noPending === 'true') return;
    if (!form.checkValidity()) return;
    const button = event.submitter instanceof HTMLElement ? event.submitter : form.querySelector('button[type="submit"],button:not([type])');
    form.classList.add('is-submitting');
    if (button && !button.dataset.keepLabel) {
      button.dataset.originalLabel = button.innerHTML;
      button.classList.add('is-pending');
      button.setAttribute('aria-busy', 'true');
      window.setTimeout(() => {
        if (button.isConnected) {
          button.disabled = true;
          button.innerHTML = '<span class="button-spinner" aria-hidden="true"></span><span>Working…</span>';
        }
      }, 60);
    }
  });

  // v3.5.1: load organization memberships from Syntal and render a real switcher.
  const organizationMenus = Array.from(document.querySelectorAll('details[data-org-switcher]'));

  function organizationLabel(org, currentOrg) {
    if (org.id === currentOrg) return 'Current workspace';
    return org.role || 'Syntal organization';
  }

  function renderOrganizations(menu, payload) {
    const options = menu.querySelector('[data-org-options]');
    const status = menu.querySelector('[data-org-status]');
    if (!options) return;
    const organizations = Array.isArray(payload.organizations) ? payload.organizations : [];
    const currentOrg = String(payload.current_org_id || menu.dataset.currentOrg || '');
    const csrfToken = menu.dataset.csrf || '';
    const nextUrl = menu.dataset.next || window.location.pathname + window.location.search;

    options.replaceChildren();
    for (const raw of organizations) {
      const id = String(raw?.id || raw?.syntal_org_id || raw?.org_id || '').trim();
      if (!id) continue;
      const name = String(raw?.name || raw?.organization_name || id).trim();
      const isCurrent = id === currentOrg;
      const form = document.createElement('form');
      form.method = 'post';
      form.action = `/auth/switch-organization/${encodeURIComponent(id)}`;
      form.className = 'organization-option-form';
      form.dataset.noPending = 'true';

      const csrf = document.createElement('input');
      csrf.type = 'hidden'; csrf.name = 'csrf_token'; csrf.value = csrfToken;
      const next = document.createElement('input');
      next.type = 'hidden'; next.name = 'next'; next.value = nextUrl;
      form.append(csrf, next);

      const button = document.createElement('button');
      button.type = 'submit';
      button.className = `organization-option${isCurrent ? ' is-current' : ''}`;
      button.disabled = isCurrent;

      const avatar = document.createElement('span');
      avatar.className = 'organization-option-avatar';
      avatar.textContent = (name || id).slice(0, 1).toUpperCase();
      const copy = document.createElement('span');
      const strong = document.createElement('strong');
      strong.textContent = name || id;
      const small = document.createElement('small');
      small.textContent = organizationLabel({id, role: raw?.role}, currentOrg);
      copy.append(strong, small);
      const mark = document.createElement('em');
      mark.textContent = isCurrent ? '✓' : '';
      button.append(avatar, copy, mark);
      form.append(button);
      options.append(form);
    }

    if (!options.children.length) {
      const empty = document.createElement('div');
      empty.className = 'organization-directory-status';
      empty.textContent = 'No organizations were returned by Syntal.';
      options.append(empty);
    }
    options.classList.remove('is-loading');

    if (status) {
      status.classList.toggle('is-connected', !!payload.connected);
      status.classList.toggle('needs-reauth', !!payload.reauth_required);
      if (payload.reauth_required) status.textContent = 'Reconnect once to load all organizations from Syntal.';
      else if (payload.connected) status.textContent = `${organizations.length} organization${organizations.length === 1 ? '' : 's'} available from Syntal`;
      else status.textContent = 'Using the last organization list saved by NJS.';
    }
  }

  async function refreshOrganizationMenu(menu) {
    if (menu.dataset.orgLoaded === '1' || menu.dataset.orgLoading === '1') return;
    const endpoint = menu.dataset.orgEndpoint;
    if (!endpoint) return;
    const options = menu.querySelector('[data-org-options]');
    menu.dataset.orgLoading = '1';
    options?.classList.add('is-loading');
    try {
      const response = await fetch(endpoint, {
        headers: {'Accept': 'application/json', 'X-NJS-Organization-Directory': '1'},
        cache: 'no-store',
        credentials: 'same-origin'
      });
      if (!response.ok) throw new Error(`Organization directory ${response.status}`);
      const payload = await response.json();
      renderOrganizations(menu, payload);
      // Update the other desktop/mobile copy of the switcher from the same payload.
      organizationMenus.filter(other => other !== menu).forEach(other => renderOrganizations(other, payload));
      organizationMenus.forEach(other => { other.dataset.orgLoaded = '1'; });
    } catch (error) {
      options?.classList.remove('is-loading');
      const status = menu.querySelector('[data-org-status]');
      if (status) {
        status.classList.add('needs-reauth');
        status.textContent = 'Could not refresh Syntal organizations. Use “Choose in Syntal” to reconnect.';
      }
    } finally {
      menu.dataset.orgLoading = '0';
    }
  }

  organizationMenus.forEach(menu => menu.addEventListener('toggle', () => {
    if (!menu.open) return;
    organizationMenus.filter(other => other !== menu).forEach(other => { other.open = false; });
    refreshOrganizationMenu(menu);
  }));

  document.addEventListener('click', event => {
    organizationMenus.forEach(menu => {
      if (menu.open && !menu.contains(event.target)) menu.open = false;
    });
  });

  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') organizationMenus.forEach(menu => { menu.open = false; });
  });

  // v2.0: workload-aware live polling. Only pages that declare data-live-watch poll.
  const watcher = document.querySelector('[data-live-watch]');
  if (!watcher) return;

  const scope = watcher.dataset.liveScope;
  const id = watcher.dataset.liveId || '';
  const activity = document.querySelector('[data-live-activity]');
  const stageNode = activity?.querySelector('[data-live-stage]');
  const detailNode = activity?.querySelector('[data-live-detail]');
  const workspaceStatus = document.querySelector('[data-workspace-status]');
  let lastToken = null;
  let lastBusy = null;
  let timer = null;
  let failures = 0;
  let stopped = false;

  function endpoint() {
    const query = new URLSearchParams({scope});
    if (id) query.set('id', id);
    return `/api/live-status?${query.toString()}`;
  }

  function setActivity(data) {
    if (!activity) return;
    activity.hidden = !data.busy;
    activity.classList.toggle('is-visible', !!data.busy);
    if (stageNode) stageNode.textContent = data.stage || 'Background work in progress';
    if (detailNode) {
      const seconds = Math.max(2, Math.round((data.next_poll_ms || 8000) / 1000));
      detailNode.textContent = `Live status · checking about every ${seconds}s · safe to leave this page`;
    }
    if (workspaceStatus) workspaceStatus.textContent = data.busy ? (data.stage || 'Working') : 'System online';
    document.body.classList.toggle('has-live-work', !!data.busy);
  }

  function schedule(ms) {
    clearTimeout(timer);
    if (stopped) return;
    const delay = Math.max(2000, Math.min(Number(ms) || 8000, 8000));
    timer = window.setTimeout(poll, document.hidden ? Math.max(delay, 15000) : delay);
  }

  async function poll() {
    if (stopped) return;
    if (document.hidden) {
      schedule(15000);
      return;
    }
    try {
      const response = await fetch(endpoint(), {
        headers: {'Accept': 'application/json', 'X-NJS-Live': '1'},
        cache: 'no-store',
        credentials: 'same-origin'
      });
      if (response.status === 401 || response.status === 403 || response.status === 404) {
        stopped = true;
        return;
      }
      if (!response.ok) throw new Error(`Live status ${response.status}`);
      const data = await response.json();
      failures = 0;
      setActivity(data);

      if (lastToken === null) {
        lastToken = data.state_token;
        lastBusy = !!data.busy;
      } else if (data.state_token && data.state_token !== lastToken) {
        // Status transitions are relatively infrequent and are the safest moment to refresh
        // the server-rendered view. Preserve scroll position for review-heavy screens.
        sessionStorage.setItem('njs-live-scroll-y', String(window.scrollY || 0));
        sessionStorage.setItem('njs-live-refresh', '1');
        window.location.reload();
        return;
      } else {
        lastBusy = !!data.busy;
      }
      schedule(data.next_poll_ms);
    } catch (error) {
      failures += 1;
      if (workspaceStatus && failures > 1) workspaceStatus.textContent = 'Reconnecting…';
      schedule(Math.min(8000, 2000 * Math.pow(2, Math.min(failures, 2))));
    }
  }

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && !stopped) {
      clearTimeout(timer);
      poll();
    }
  });

  if (sessionStorage.getItem('njs-live-refresh') === '1') {
    sessionStorage.removeItem('njs-live-refresh');
    const y = Number(sessionStorage.getItem('njs-live-scroll-y') || 0);
    sessionStorage.removeItem('njs-live-scroll-y');
    requestAnimationFrame(() => window.scrollTo({top: y, behavior: 'auto'}));
  }

  poll();
})();

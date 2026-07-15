/* SAA-139: Client-side auth/RBAC route guard, shared across all static pages. */

async function guardPage(allowedRoles) {
  let user;
  try {
    const res = await fetch('/api/auth/me');
    if (res.status === 401) {
      const next = encodeURIComponent(location.pathname);
      location.replace(`/static/login.html?next=${next}`);
      return null;
    }
    if (!res.ok) throw new Error(res.statusText);
    user = await res.json();
  } catch (e) {
    console.error('auth check failed', e);
    location.replace('/static/login.html');
    return null;
  }

  if (allowedRoles && allowedRoles.length && !allowedRoles.includes(user.role)) {
    location.replace('/static/dashboard.html');
    return null;
  }

  window.currentUser = user;
  renderUserBadge(user);
  return user;
}

function renderUserBadge(user) {
  const header = document.querySelector('.header-left');
  if (!header) return;
  const roleLabels = { admin: 'מנהל', operator: 'אופרטור', analyst: 'אנליסט' };
  const badge = document.createElement('div');
  badge.className = 'top-badge blue';
  badge.textContent = `${user.email} · ${roleLabels[user.role] || user.role}`;
  const logout = document.createElement('button');
  logout.className = 'btn-secondary';
  logout.style.padding = '4px 12px';
  logout.style.fontSize = '12px';
  logout.textContent = 'התנתק';
  logout.onclick = async () => {
    await fetch('/api/auth/logout', { method: 'POST' });
    location.replace('/static/login.html');
  };
  header.prepend(logout);
  header.prepend(badge);
}

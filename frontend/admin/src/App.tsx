import {
  Activity,
  BarChart3,
  CalendarDays,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Database,
  Eye,
  FileText,
  History,
  LockKeyhole,
  LogOut,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  TrendingUp,
  UserCheck,
  UserX,
  Users,
  X,
} from "lucide-react";
import { FormEvent, ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type SessionInfo = {
  authenticated: boolean;
  admin: string;
  csrf_token: string;
  configured: boolean;
  timezone: string;
};

type VerdictOption = {
  key: string;
  label: string;
};

type Pagination = {
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  has_prev: boolean;
  has_next: boolean;
};

type RequestItem = {
  request_id: string;
  identity_key: string;
  time: string;
  user: string;
  audit_enabled: boolean;
  token: string | number;
  model: string;
  tokens: number;
  tokens_compact: string;
  quota: number;
  quota_compact: string;
  verdict: string;
  verdict_label: string;
  category: string;
  confidence: number;
  review_status: string;
  reason: string;
  preview: string;
  preview_short: string;
};

type UserItem = {
  identity_key: string;
  user_id: number | null;
  username: string;
  display_name: string;
  display_label: string;
  audit_enabled: boolean;
  notes: string;
  request_count: number;
  total_tokens: number;
  total_tokens_compact: string;
  first_seen_at: string;
  last_seen_at: string;
  top_model: string;
  configured: boolean;
};

type UserDetail = {
  identity_key: string;
  user_id: number | null;
  username: string;
  display_name: string;
  display_label: string;
  audit_enabled: boolean;
  notes: string;
  first_seen_at: string;
  last_seen_at: string;
};

type DashboardData = {
  date: string;
  user_count: number;
  enabled_user_count: number;
  unconfigured_user_count: number;
  all_request_count: number;
  all_total_tokens: number;
  today_request_count: number;
  today_total_tokens: number;
  today_quota: number;
  top_users: Array<{
    identity_key: string;
    name: string;
    tokens: number;
    prompt_tokens: number;
    completion_tokens: number;
    quota: number;
    requests: number;
    audit_enabled: boolean;
    top_model: string;
    top_token: string;
  }>;
};

type RequestOptions = {
  users?: Array<{ identity_key: string; name: string }>;
  tokens: string[];
  models: string[];
  verdicts: VerdictOption[];
};

type UserStats = {
  request_count: number;
  total_tokens: number;
  total_tokens_compact: string;
  quota: number;
  top_model: string;
  top_token: string;
  first_seen_at: string;
  last_seen_at: string;
};

type PreviewState = {
  title: string;
  text: string;
  meta?: string;
  loading?: boolean;
  error?: string;
} | null;

const NAV_ITEMS = [
  { path: "/admin/dashboard", label: "总览", icon: BarChart3 },
  { path: "/admin/users", label: "用户", icon: Users },
  { path: "/admin/requests", label: "请求", icon: History },
  { path: "/admin/reports/daily", label: "日报", icon: FileText },
];

function App() {
  const [path, setPath] = useState(currentPath());
  const [search, setSearch] = useState(currentSearch());
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [loading, setLoading] = useState(true);

  const navigate = useCallback((to: string) => {
    window.history.pushState({}, "", to);
    setPath(currentPath());
    setSearch(currentSearch());
  }, []);

  const refreshSession = useCallback(async () => {
    const next = await apiFetch<SessionInfo>("/admin/api/session");
    setSession(next);
    return next;
  }, []);

  useEffect(() => {
    refreshSession().finally(() => setLoading(false));
    const onPop = () => {
      setPath(currentPath());
      setSearch(currentSearch());
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [refreshSession]);

  useEffect(() => {
    if (loading || !session) return;
    if (!session.authenticated && path !== "/admin/login") {
      navigate("/admin/login");
    }
    if (session.authenticated && (path === "/admin" || path === "/admin/login")) {
      navigate("/admin/dashboard");
    }
  }, [loading, navigate, path, session]);

  if (loading || !session) {
    return <Splash />;
  }

  if (!session.authenticated) {
    return <LoginPage configured={session.configured} onLoggedIn={refreshSession} navigate={navigate} />;
  }

  return (
    <AdminLayout path={path} session={session} navigate={navigate} refreshSession={refreshSession}>
      <RouteRenderer path={path} search={search} session={session} navigate={navigate} />
    </AdminLayout>
  );
}

function RouteRenderer({
  path,
  search,
  session,
  navigate,
}: {
  path: string;
  search: string;
  session: SessionInfo;
  navigate: (to: string) => void;
}) {
  if (path.startsWith("/admin/users/")) {
    const identityKey = decodeURIComponent(path.slice("/admin/users/".length).split("/")[0] || "");
    return <UserDetailPage identityKey={identityKey} session={session} navigate={navigate} />;
  }
  if (path === "/admin/users") {
    return <UsersPage session={session} navigate={navigate} />;
  }
  if (path === "/admin/requests") {
    return <RequestsPage session={session} search={search} navigate={navigate} />;
  }
  if (path === "/admin/reports/daily") {
    return <DailyReportPage />;
  }
  return <DashboardPage navigate={navigate} />;
}

function Splash() {
  return (
    <div className="app-shell grid min-h-screen place-items-center px-6">
      <div className="glass-card max-w-sm p-8 text-center">
        <div className="mx-auto mb-5 flex h-14 w-14 items-center justify-center rounded-2xl border border-cyan-200/25 bg-cyan-300/10 text-cyan-100 shadow-glow">
          <Sparkles className="h-7 w-7" />
        </div>
        <h1 className="text-xl font-semibold text-white">Token Audit</h1>
        <p className="mt-2 text-sm text-slate-300">正在打开管理端</p>
      </div>
    </div>
  );
}

function LoginPage({
  configured,
  onLoggedIn,
  navigate,
}: {
  configured: boolean;
  onLoggedIn: () => Promise<SessionInfo>;
  navigate: (to: string) => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      await apiFetch("/admin/api/login", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      await onLoggedIn();
      navigate("/admin/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="app-shell grid min-h-screen place-items-center px-5 py-8">
      <div className="grid w-full max-w-5xl gap-6 lg:grid-cols-[1.05fr_0.95fr]">
        <section className="glass-card flex min-h-[420px] flex-col justify-between overflow-hidden p-7 sm:p-10">
          <div>
            <div className="mb-8 inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/10 px-3 py-1 text-xs text-cyan-100">
              <ShieldCheck className="h-4 w-4" />
              Token Audit Command Center
            </div>
            <h1 className="max-w-xl text-4xl font-semibold leading-tight text-white sm:text-5xl">透明、克制、可追溯的 AI 用量审计</h1>
            <p className="mt-5 max-w-2xl text-base leading-7 text-slate-300">
              管理统计用户、请求历史和日报入口都在同一处。后台仍使用 SQLite 和 FastAPI，前端只负责更轻盈的操作体验。
            </p>
          </div>
          <div className="grid gap-3 text-sm text-slate-300 sm:grid-cols-3">
            {[
              ["30 天", "默认明细留存"],
              ["SQLite", "轻量单容器部署"],
              ["只读预览", "不解密完整 Prompt"],
            ].map(([value, label]) => (
              <div key={label} className="rounded-2xl border border-white/10 bg-white/[0.06] p-4">
                <div className="text-2xl font-semibold text-white">{value}</div>
                <div className="mt-1">{label}</div>
              </div>
            ))}
          </div>
        </section>
        <form onSubmit={submit} className="glass-card p-7 sm:p-9">
          <div className="mb-8 flex h-14 w-14 items-center justify-center rounded-2xl border border-violet-200/25 bg-violet-300/10 text-violet-100">
            <LockKeyhole className="h-7 w-7" />
          </div>
          <h2 className="text-2xl font-semibold text-white">管理员登录</h2>
          <p className="mt-2 text-sm text-slate-300">使用服务器环境变量中配置的管理账号。</p>
          {!configured && (
            <div className="mt-5 rounded-2xl border border-amber-200/25 bg-amber-300/10 p-4 text-sm text-amber-100">
              管理端账号未配置，请先设置 AUDIT_ADMIN_USER 和 AUDIT_ADMIN_PASSWORD。
            </div>
          )}
          {error && <ErrorBanner message={error} />}
          <label className="mt-7 block text-sm text-slate-300">
            用户名
            <input className="glass-input mt-2" value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" />
          </label>
          <label className="mt-4 block text-sm text-slate-300">
            密码
            <input
              className="glass-input mt-2"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              autoComplete="current-password"
            />
          </label>
          <button className="glass-button mt-7 w-full" type="submit" disabled={submitting || !configured}>
            <ShieldCheck className="h-4 w-4" />
            {submitting ? "登录中" : "进入管理端"}
          </button>
        </form>
      </div>
    </div>
  );
}

function AdminLayout({
  path,
  session,
  navigate,
  refreshSession,
  children,
}: {
  path: string;
  session: SessionInfo;
  navigate: (to: string) => void;
  refreshSession: () => Promise<SessionInfo>;
  children: ReactNode;
}) {
  async function logout() {
    await apiFetch("/admin/api/logout", { method: "POST" }, session.csrf_token);
    await refreshSession();
    navigate("/admin/login");
  }

  return (
    <div className="app-shell min-h-screen">
      <div className="mx-auto min-h-screen w-full max-w-[1520px] px-4 py-4 sm:px-5 lg:px-7 lg:py-7">
        <aside className="glass-card sticky top-4 z-20 flex h-fit min-w-0 flex-col gap-5 p-4 lg:fixed lg:left-[max(1.75rem,calc((100vw-1520px)/2+1.75rem))] lg:top-7 lg:h-[calc(100vh-56px)] lg:w-[260px] lg:overflow-y-auto">
          <div className="flex items-center gap-3 px-2">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border border-cyan-200/25 bg-cyan-300/10 text-cyan-100 shadow-glow">
              <Activity className="h-6 w-6" />
            </div>
            <div>
              <div className="text-sm text-slate-400">Token Audit</div>
              <div className="font-semibold text-white">审计管理端</div>
            </div>
          </div>
          <nav className="flex gap-2 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible">
            {NAV_ITEMS.map((item) => (
              <button
                key={item.path}
                onClick={() => navigate(item.path)}
                className={[
                  "group flex min-w-max items-center gap-3 rounded-2xl border px-4 py-3 text-sm transition duration-200 lg:min-w-0",
                  isActiveNav(path, item.path)
                    ? "border-cyan-200/35 bg-cyan-300/15 text-cyan-50 shadow-glow"
                    : "border-white/10 bg-white/[0.04] text-slate-300 hover:border-white/20 hover:bg-white/[0.08] hover:text-white",
                ].join(" ")}
              >
                <item.icon className="h-4 w-4" />
                {item.label}
              </button>
            ))}
          </nav>
          <div className="mt-auto hidden rounded-2xl border border-white/10 bg-white/[0.05] p-4 text-sm text-slate-300 lg:block">
            <div className="text-xs uppercase tracking-wider text-slate-500">Admin</div>
            <div className="mt-1 truncate text-white">{session.admin}</div>
            <button className="glass-button-muted mt-4 w-full" onClick={logout}>
              <LogOut className="h-4 w-4" />
              退出
            </button>
          </div>
          <button className="glass-button-muted lg:hidden" onClick={logout}>
            <LogOut className="h-4 w-4" />
            退出
          </button>
        </aside>
        <main className="min-w-0 pb-8 lg:ml-[280px]">{children}</main>
      </div>
    </div>
  );
}

function DashboardPage({ navigate }: { navigate: (to: string) => void }) {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [lastRefreshedAt, setLastRefreshedAt] = useState("");

  const loadDashboard = useCallback(async () => {
    setError("");
    try {
      const payload = await apiFetch<DashboardData>("/admin/api/dashboard");
      setData(payload);
      setLastRefreshedAt(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法加载总览");
    }
  }, []);

  useEffect(() => {
    void loadDashboard();
  }, [loadDashboard]);

  useEffect(() => {
    if (!autoRefresh) return;
    const timer = window.setInterval(() => {
      void loadDashboard();
    }, 60_000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, loadDashboard]);

  if (!data && error) return <ErrorBanner message={error} />;
  if (!data) return <LoadingCard label="正在加载审计总览" />;

  const topTokenMax = Math.max(...data.top_users.map((user) => user.tokens), 1);

  return (
    <div className="space-y-5">
      <PageTitle
        eyebrow={data.date}
        title="审计总览"
        subtitle="查看今日请求、用户配置和高消耗用户。日报与企业微信推送仍由后端任务生成。"
        action={
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <button
              className={autoRefresh ? "glass-button" : "glass-button-muted"}
              onClick={() => setAutoRefresh((enabled) => !enabled)}
              title="开启后每 1 分钟刷新一次审计总览统计"
            >
              <RefreshCw className={`h-4 w-4 ${autoRefresh ? "animate-spin" : ""}`} />
              {autoRefresh ? "停止刷新" : "定时刷新"}
            </button>
            <button className="glass-button" onClick={() => navigate("/admin/reports/daily")}>
              <CalendarDays className="h-4 w-4" />
              打开日报
            </button>
          </div>
        }
      />
      {(error || lastRefreshedAt) && (
        <div className="rounded-2xl border border-white/10 bg-white/[0.05] px-4 py-3 text-sm text-slate-300 backdrop-blur">
          {error ? <span className="text-rose-100">刷新失败：{error}</span> : <span>最后刷新：{lastRefreshedAt}</span>}
          {autoRefresh && <span className="ml-3 text-cyan-100">已开启 1 分钟自动刷新</span>}
        </div>
      )}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard icon={Database} label="历史请求" value={compact(data.all_request_count)} sub={`${compact(data.all_total_tokens)} Tokens`} tone="cyan" />
        <MetricCard icon={TrendingUp} label="今日 Tokens" value={compact(data.today_total_tokens)} sub={`${data.today_request_count} 次请求`} tone="violet" />
        <MetricCard icon={UserCheck} label="纳入审计用户" value={compact(data.enabled_user_count)} sub={`共发现 ${data.user_count} 人`} tone="teal" />
        <MetricCard icon={Database} label="今日 Quota" value={compact(data.today_quota)} sub="按 New-API 结算口径" tone="rose" />
      </div>
      <GlassCard>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <SectionHeader icon={Users} title="今日 Top5 用量" subtitle="按当日总 Tokens 排序，展示请求量、Token 构成、Quota、主要模型和 Token 名称。" />
          <div className="rounded-2xl border border-white/10 bg-white/[0.045] px-4 py-3 text-sm text-slate-300">
            今日合计 <span className="font-semibold text-white">{compact(data.today_total_tokens)}</span> Tokens
          </div>
        </div>
        <div className="mt-5 space-y-3">
          {data.top_users.length === 0 && <EmptyState label="今日暂无请求" />}
          {data.top_users.map((user, index) => (
            <button
              key={user.identity_key}
              className="group w-full overflow-hidden rounded-2xl border border-white/10 bg-white/[0.045] p-4 text-left transition duration-200 hover:border-cyan-200/30 hover:bg-white/[0.08] hover:shadow-glow sm:p-5"
              onClick={() => navigate(`/admin/requests?user=${encodeURIComponent(user.identity_key)}`)}
            >
              <div className="grid gap-4 xl:grid-cols-[minmax(0,1.05fr)_minmax(0,1.6fr)] xl:items-center">
                <div className="flex min-w-0 items-start gap-4">
                  <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-cyan-200/20 bg-cyan-300/10 text-lg font-semibold text-cyan-50">
                    {index + 1}
                  </div>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="truncate text-lg font-semibold text-white">{user.name}</span>
                      <StatusPill verdict={user.audit_enabled ? "work" : "uncertain"} label={user.audit_enabled ? "已纳入" : "未纳入"} />
                    </div>
                    <div className="mt-2 grid gap-2 text-sm text-slate-400 sm:grid-cols-2">
                      <span className="truncate">模型：{user.top_model}</span>
                      <span className="truncate">Token：{user.top_token}</span>
                    </div>
                  </div>
                </div>
                <div className="grid gap-3 sm:grid-cols-4">
                  <TopUsageMetric label="总 Tokens" value={compact(user.tokens)} emphasize />
                  <TopUsageMetric label="请求数" value={`${user.requests} 次`} />
                  <TopUsageMetric label="Prompt / Completion" value={`${compact(user.prompt_tokens)} / ${compact(user.completion_tokens)}`} />
                  <TopUsageMetric label="Quota" value={compact(user.quota)} />
                </div>
              </div>
              <div className="mt-4 h-2 overflow-hidden rounded-full bg-white/10">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-cyan-300 via-teal-300 to-violet-300 transition-all duration-300"
                  style={{ width: `${Math.max(6, Math.round((user.tokens / topTokenMax) * 100))}%` }}
                />
              </div>
            </button>
          ))}
        </div>
      </GlassCard>
    </div>
  );
}

function TopUsageMetric({ label, value, emphasize = false }: { label: string; value: string; emphasize?: boolean }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.04] px-3 py-3">
      <div className="text-xs text-slate-500">{label}</div>
      <div className={["mt-1 truncate font-semibold", emphasize ? "text-xl text-cyan-50" : "text-sm text-white"].join(" ")}>{value}</div>
    </div>
  );
}

function UsersPage({ session, navigate }: { session: SessionInfo; navigate: (to: string) => void }) {
  const [users, setUsers] = useState<UserItem[]>([]);
  const [status, setStatus] = useState("all");
  const [configured, setConfigured] = useState("all");
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const query = new URLSearchParams({ status, configured, q });
      const payload = await apiFetch<{ users: UserItem[] }>(`/admin/api/users?${query}`);
      setUsers(payload.users);
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法加载用户");
    } finally {
      setLoading(false);
    }
  }, [configured, q, status]);

  useEffect(() => {
    load();
  }, [load]);

  async function syncUsers() {
    const payload = await apiFetch<{ created: number }>("/admin/api/users/sync", { method: "POST" }, session.csrf_token);
    await load();
    window.alert(`同步完成，新发现 ${payload.created} 个用户`);
  }

  async function saveUser(identityKey: string, patch: Partial<UserItem>) {
    const payload = await apiFetch<{ user: UserDetail }>(
      `/admin/api/users/${encodeURIComponent(identityKey)}`,
      {
        method: "PATCH",
        body: JSON.stringify(patch),
      },
      session.csrf_token,
    );
    setUsers((current) =>
      current.map((user) =>
        user.identity_key === identityKey
          ? {
              ...user,
              display_name: payload.user.display_name,
              display_label: payload.user.display_label,
              audit_enabled: payload.user.audit_enabled,
              notes: payload.user.notes,
              configured: Boolean(payload.user.display_name),
            }
          : user,
      ),
    );
  }

  return (
    <div className="space-y-5">
      <PageTitle
        eyebrow="User Registry"
        title="审计用户"
        subtitle="历史请求会自动发现用户，新用户默认不纳入日报。管理员启用后才进入统计。"
        action={
          <button className="glass-button" onClick={syncUsers}>
            <RefreshCw className="h-4 w-4" />
            同步历史用户
          </button>
        }
      />
      <GlassCard>
        <div className="grid gap-3 md:grid-cols-[1fr_160px_180px_auto]">
          <label className="relative">
            <Search className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-slate-400" />
            <input className="glass-input pl-9" placeholder="搜索用户、姓名、备注" value={q} onChange={(event) => setQ(event.target.value)} />
          </label>
          <select className="glass-input" value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="all">全部状态</option>
            <option value="enabled">已纳入</option>
            <option value="disabled">未纳入</option>
          </select>
          <select className="glass-input" value={configured} onChange={(event) => setConfigured(event.target.value)}>
            <option value="all">全部姓名</option>
            <option value="configured">已设置姓名</option>
            <option value="unconfigured">未设置姓名</option>
          </select>
          <button className="glass-button-muted" onClick={load}>
            <Search className="h-4 w-4" />
            查询
          </button>
        </div>
      </GlassCard>
      {error && <ErrorBanner message={error} />}
      {loading ? (
        <LoadingCard label="正在加载用户" />
      ) : (
        <GlassCard>
          <div className="mb-4 flex items-center justify-between gap-3">
            <SectionHeader icon={Users} title="用户配置" subtitle={`${users.length} 个用户匹配当前筛选`} />
          </div>
          <div className="hidden lg:block">
            <div className="table-shell">
              <table>
                <thead>
                  <tr>
                    <th>用户</th>
                    <th>显示名</th>
                    <th>纳入日报</th>
                    <th>请求 / Tokens</th>
                    <th>备注</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((user) => (
                    <EditableUserRow key={user.identity_key} user={user} saveUser={saveUser} navigate={navigate} />
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <div className="grid gap-3 lg:hidden">
            {users.map((user) => (
              <EditableUserCard key={user.identity_key} user={user} saveUser={saveUser} navigate={navigate} />
            ))}
          </div>
          {users.length === 0 && <EmptyState label="没有匹配的用户" />}
        </GlassCard>
      )}
    </div>
  );
}

function EditableUserRow({
  user,
  saveUser,
  navigate,
}: {
  user: UserItem;
  saveUser: (identityKey: string, patch: Partial<UserItem>) => Promise<void>;
  navigate: (to: string) => void;
}) {
  const editor = useUserEditor(user, saveUser);
  return (
    <tr>
      <td>
        <button className="text-left" onClick={() => navigate(`/admin/users/${encodeURIComponent(user.identity_key)}`)}>
          <div className="font-medium text-white">{user.display_label}</div>
          <div className="mt-1 text-xs text-slate-400">
            ID: {user.user_id ?? "-"} / {user.username || "unknown"}
          </div>
          <div className="mt-1 text-xs text-slate-500">最近: {user.last_seen_at || "-"}</div>
        </button>
      </td>
      <td>
        <input className="glass-input min-w-40" value={editor.displayName} onChange={(event) => editor.setDisplayName(event.target.value)} />
      </td>
      <td>
        <label className="inline-flex cursor-pointer items-center gap-2 text-sm">
          <input className="h-4 w-4 accent-teal-300" checked={editor.auditEnabled} onChange={(event) => editor.setAuditEnabled(event.target.checked)} type="checkbox" />
          {editor.auditEnabled ? "已纳入" : "不纳入"}
        </label>
      </td>
      <td>
        <div className="font-medium text-white">{user.request_count} 次</div>
        <div className="text-sm text-cyan-100">{user.total_tokens_compact} Tokens</div>
        <div className="text-xs text-slate-400">{user.top_model}</div>
      </td>
      <td>
        <textarea className="glass-input min-h-20 min-w-48" value={editor.notes} onChange={(event) => editor.setNotes(event.target.value)} />
      </td>
      <td>
        <button className="glass-button" onClick={editor.save} disabled={!editor.dirty || editor.saving}>
          {editor.saving ? "保存中" : "保存"}
        </button>
      </td>
    </tr>
  );
}

function EditableUserCard({
  user,
  saveUser,
  navigate,
}: {
  user: UserItem;
  saveUser: (identityKey: string, patch: Partial<UserItem>) => Promise<void>;
  navigate: (to: string) => void;
}) {
  const editor = useUserEditor(user, saveUser);
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.045] p-4">
      <button className="w-full text-left" onClick={() => navigate(`/admin/users/${encodeURIComponent(user.identity_key)}`)}>
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="truncate font-medium text-white">{user.display_label}</div>
            <div className="mt-1 text-xs text-slate-400">
              ID: {user.user_id ?? "-"} / {user.username || "unknown"}
            </div>
          </div>
          <StatusPill verdict={user.audit_enabled ? "work" : "uncertain"} label={user.audit_enabled ? "已纳入" : "未纳入"} />
        </div>
      </button>
      <div className="mt-4 grid gap-3">
        <input className="glass-input" value={editor.displayName} onChange={(event) => editor.setDisplayName(event.target.value)} placeholder="日报显示名" />
        <label className="inline-flex items-center gap-2 text-sm text-slate-300">
          <input className="h-4 w-4 accent-teal-300" checked={editor.auditEnabled} onChange={(event) => editor.setAuditEnabled(event.target.checked)} type="checkbox" />
          纳入审计日报
        </label>
        <textarea className="glass-input min-h-20" value={editor.notes} onChange={(event) => editor.setNotes(event.target.value)} placeholder="备注" />
        <div className="flex items-center justify-between gap-3">
          <div className="text-sm text-slate-300">
            {user.request_count} 次 / {user.total_tokens_compact}
          </div>
          <button className="glass-button" onClick={editor.save} disabled={!editor.dirty || editor.saving}>
            保存
          </button>
        </div>
      </div>
    </div>
  );
}

function useUserEditor(user: UserItem | UserDetail, saveUser: (identityKey: string, patch: Partial<UserItem>) => Promise<void>) {
  const [displayName, setDisplayName] = useState(user.display_name || "");
  const [auditEnabled, setAuditEnabled] = useState(user.audit_enabled);
  const [notes, setNotes] = useState(user.notes || "");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setDisplayName(user.display_name || "");
    setAuditEnabled(user.audit_enabled);
    setNotes(user.notes || "");
  }, [user.audit_enabled, user.display_name, user.identity_key, user.notes]);

  const dirty = displayName !== (user.display_name || "") || auditEnabled !== user.audit_enabled || notes !== (user.notes || "");

  async function save() {
    setSaving(true);
    try {
      await saveUser(user.identity_key, { display_name: displayName, audit_enabled: auditEnabled, notes });
    } finally {
      setSaving(false);
    }
  }

  return { displayName, setDisplayName, auditEnabled, setAuditEnabled, notes, setNotes, saving, dirty, save };
}

function UserDetailPage({
  identityKey,
  session,
  navigate,
}: {
  identityKey: string;
  session: SessionInfo;
  navigate: (to: string) => void;
}) {
  const [user, setUser] = useState<UserDetail | null>(null);
  const [stats, setStats] = useState<UserStats | null>(null);
  const [requests, setRequests] = useState<RequestItem[]>([]);
  const [pagination, setPagination] = useState<Pagination | null>(null);
  const [options, setOptions] = useState<RequestOptions | null>(null);
  const [filters, setFilters] = useState({ start: "", end: "", verdict: "", token: "", model: "" });
  const [page, setPage] = useState(1);
  const [preview, setPreview] = useState<PreviewState>(null);
  const [error, setError] = useState("");

  const loadDetail = useCallback(async () => {
    const payload = await apiFetch<{ user: UserDetail; stats: UserStats; options: RequestOptions }>(`/admin/api/users/${encodeURIComponent(identityKey)}`);
    setUser(payload.user);
    setStats(payload.stats);
    setOptions(payload.options);
  }, [identityKey]);

  const loadRequests = useCallback(async () => {
    const query = new URLSearchParams({ ...filters, page: String(page) });
    const payload = await apiFetch<{ requests: RequestItem[]; pagination: Pagination; options: RequestOptions }>(
      `/admin/api/users/${encodeURIComponent(identityKey)}/requests?${query}`,
    );
    setRequests(payload.requests);
    setPagination(payload.pagination);
    setOptions(payload.options);
  }, [filters, identityKey, page]);

  useEffect(() => {
    setError("");
    Promise.all([loadDetail(), loadRequests()]).catch((err) => setError(err instanceof Error ? err.message : "无法加载用户详情"));
  }, [loadDetail, loadRequests]);

  async function saveUser(_identityKey: string, patch: Partial<UserItem>) {
    const payload = await apiFetch<{ user: UserDetail }>(
      `/admin/api/users/${encodeURIComponent(identityKey)}`,
      { method: "PATCH", body: JSON.stringify(patch) },
      session.csrf_token,
    );
    setUser(payload.user);
    await loadDetail();
  }

  if (error) return <ErrorBanner message={error} />;
  if (!user || !stats || !options || !pagination) return <LoadingCard label="正在加载用户详情" />;

  return (
    <UserDetailLoaded
      user={user}
      stats={stats}
      options={options}
      requests={requests}
      pagination={pagination}
      filters={filters}
      setFilters={setFilters}
      setPage={setPage}
      setPreview={setPreview}
      saveUser={saveUser}
      navigate={navigate}
      preview={preview}
    />
  );
}

function UserDetailLoaded({
  user,
  stats,
  options,
  requests,
  pagination,
  filters,
  setFilters,
  setPage,
  setPreview,
  saveUser,
  navigate,
  preview,
}: {
  user: UserDetail;
  stats: UserStats;
  options: RequestOptions;
  requests: RequestItem[];
  pagination: Pagination;
  filters: Record<string, string>;
  setFilters: (filters: any) => void;
  setPage: (page: number) => void;
  setPreview: (preview: PreviewState) => void;
  saveUser: (identityKey: string, patch: Partial<UserItem>) => Promise<void>;
  navigate: (to: string) => void;
  preview: PreviewState;
}) {
  const editor = useUserEditor(user, saveUser);

  return (
    <div className="space-y-5">
      <PageTitle
        eyebrow="User Detail"
        title={user.display_label}
        subtitle={`ID: ${user.user_id ?? "-"} / ${user.username || "unknown"}`}
        action={
          <button className="glass-button-muted" onClick={() => navigate("/admin/users")}>
            <ChevronLeft className="h-4 w-4" />
            返回用户
          </button>
        }
      />
      <div className="grid gap-5 xl:grid-cols-[0.85fr_1.15fr]">
        <GlassCard>
          <SectionHeader icon={UserCheck} title="用户配置" subtitle="显示名会用于日报和企业微信详情页。" />
          <div className="mt-5 grid gap-3">
            <input className="glass-input" value={editor.displayName} onChange={(event) => editor.setDisplayName(event.target.value)} placeholder="日报显示名" />
            <label className="inline-flex items-center gap-2 text-sm text-slate-300">
              <input className="h-4 w-4 accent-teal-300" checked={editor.auditEnabled} onChange={(event) => editor.setAuditEnabled(event.target.checked)} type="checkbox" />
              纳入审计日报
            </label>
            <textarea className="glass-input min-h-28" value={editor.notes} onChange={(event) => editor.setNotes(event.target.value)} placeholder="备注" />
            <button className="glass-button w-fit" onClick={editor.save} disabled={!editor.dirty || editor.saving}>
              {editor.saving ? "保存中" : "保存配置"}
            </button>
          </div>
        </GlassCard>
        <div className="grid gap-4 sm:grid-cols-2">
          <MetricCard icon={History} label="请求数" value={compact(stats.request_count)} sub={`最近 ${stats.last_seen_at || "-"}`} tone="cyan" />
          <MetricCard icon={TrendingUp} label="总 Tokens" value={stats.total_tokens_compact} sub={`Quota ${compact(stats.quota)}`} tone="teal" />
          <MetricCard icon={Database} label="主要模型" value={stats.top_model} sub={`Token ${stats.top_token}`} tone="violet" />
          <MetricCard icon={CalendarDays} label="首次出现" value={stats.first_seen_at ? stats.first_seen_at.slice(0, 10) : "-"} sub={stats.first_seen_at || "-"} tone="rose" />
        </div>
      </div>
      <GlassCard>
        <SectionHeader icon={History} title="请求历史" subtitle="仅显示 prompt_preview，不提供完整 Prompt 解密。" />
        <RequestFilters filters={filters} setFilters={setFilters} options={options} includeUser={false} onSearch={() => setPage(1)} />
        <RequestList requests={requests} navigate={navigate} setPreview={setPreview} />
        <PaginationControls pagination={pagination} setPage={setPage} />
      </GlassCard>
      <PromptModal preview={preview} close={() => setPreview(null)} />
    </div>
  );
}

function RequestsPage({ session, search, navigate }: { session: SessionInfo; search: string; navigate: (to: string) => void }) {
  const [requests, setRequests] = useState<RequestItem[]>([]);
  const [pagination, setPagination] = useState<Pagination | null>(null);
  const [options, setOptions] = useState<RequestOptions>({ users: [], tokens: [], models: [], verdicts: [] });
  const [filters, setFilters] = useState(() => parseRequestFilters(search));
  const [page, setPage] = useState(() => parseRequestPage(search));
  const [preview, setPreview] = useState<PreviewState>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const appliedSearchRef = useRef(search);

  useEffect(() => {
    if (search === appliedSearchRef.current) return;
    appliedSearchRef.current = search;
    setFilters(parseRequestFilters(search));
    setPage(parseRequestPage(search));
  }, [search]);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const query = new URLSearchParams({ ...filters, page: String(page) });
      const payload = await apiFetch<{ requests: RequestItem[]; pagination: Pagination; options: RequestOptions }>(`/admin/api/requests?${query}`);
      setRequests(payload.requests);
      setPagination(payload.pagination);
      setOptions(payload.options);
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法加载请求历史");
    } finally {
      setLoading(false);
    }
  }, [filters, page]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="space-y-5">
      <PageTitle
        eyebrow="Request Stream"
        title="请求历史"
        subtitle="按用户、Token、模型和分类结论筛选请求，点击 Prompt 预览查看详情。"
        action={
          <button className="glass-button-muted" onClick={load}>
            <RefreshCw className="h-4 w-4" />
            刷新
          </button>
        }
      />
      <GlassCard>
        <RequestFilters filters={filters} setFilters={setFilters} options={options} includeUser onSearch={() => setPage(1)} />
      </GlassCard>
      {error && <ErrorBanner message={error} />}
      {loading || !pagination ? (
        <LoadingCard label="正在加载请求历史" />
      ) : (
        <GlassCard>
          <SectionHeader icon={History} title="请求列表" subtitle={`${pagination.total} 条记录匹配当前筛选`} />
          <RequestList requests={requests} navigate={navigate} setPreview={setPreview} />
          <PaginationControls pagination={pagination} setPage={setPage} />
        </GlassCard>
      )}
      <PromptModal preview={preview} close={() => setPreview(null)} />
    </div>
  );
}

function RequestFilters({
  filters,
  setFilters,
  options,
  includeUser,
  onSearch,
}: {
  filters: Record<string, string>;
  setFilters: (filters: any) => void;
  options: RequestOptions;
  includeUser: boolean;
  onSearch: () => void;
}) {
  function update(key: string, value: string) {
    setFilters((current: Record<string, string>) => ({ ...current, [key]: value }));
  }

  return (
    <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-7">
      <input className="glass-input" type="date" value={filters.start || ""} onChange={(event) => update("start", event.target.value)} />
      <input className="glass-input" type="date" value={filters.end || ""} onChange={(event) => update("end", event.target.value)} />
      {includeUser && (
        <select className="glass-input" value={filters.user || ""} onChange={(event) => update("user", event.target.value)}>
          <option value="">全部用户</option>
          {(options.users || []).map((user) => (
            <option key={user.identity_key} value={user.identity_key}>
              {user.name}
            </option>
          ))}
        </select>
      )}
      <select className="glass-input" value={filters.verdict || ""} onChange={(event) => update("verdict", event.target.value)}>
        <option value="">全部结论</option>
        {options.verdicts.map((item) => (
          <option key={item.key} value={item.key}>
            {item.label}
          </option>
        ))}
      </select>
      <select className="glass-input" value={filters.token || ""} onChange={(event) => update("token", event.target.value)}>
        <option value="">全部 Token</option>
        {options.tokens.map((token) => (
          <option key={token} value={token}>
            {token}
          </option>
        ))}
      </select>
      <select className="glass-input" value={filters.model || ""} onChange={(event) => update("model", event.target.value)}>
        <option value="">全部模型</option>
        {options.models.map((model) => (
          <option key={model} value={model}>
            {model}
          </option>
        ))}
      </select>
      {"q" in filters && <input className="glass-input" value={filters.q || ""} onChange={(event) => update("q", event.target.value)} placeholder="Prompt / request_id" />}
      <button className="glass-button" onClick={onSearch}>
        <Search className="h-4 w-4" />
        查询
      </button>
    </div>
  );
}

function RequestsPreview({ title, requests, navigate }: { title: string; requests: RequestItem[]; navigate: (to: string) => void }) {
  const [preview, setPreview] = useState<PreviewState>(null);
  return (
    <GlassCard>
      <SectionHeader icon={History} title={title} subtitle="点击 Prompt 可以查看完整 Prompt，列表中仍只显示短摘要。" />
      <RequestList requests={requests} navigate={navigate} setPreview={setPreview} />
      <PromptModal preview={preview} close={() => setPreview(null)} />
    </GlassCard>
  );
}

async function openPromptPreview(item: RequestItem, setPreview: (preview: PreviewState) => void) {
  const initialMeta = `${item.user} / ${item.model}`;
  setPreview({ title: item.request_id, text: "", meta: initialMeta, loading: true });
  try {
    const detail = await apiFetch<{
      prompt_preview: string;
      prompt_text: string;
      prompt_source: string;
      decrypt_error: string;
      prompt_len: number;
      prompt_omitted: boolean;
      user: string;
      model: string;
      token: string | number;
      time: string;
    }>(`/admin/api/requests/${encodeURIComponent(item.request_id)}/preview`);
    const sourceNote =
      detail.prompt_source === "encrypted_full"
        ? "完整 Prompt"
        : detail.prompt_source === "omitted_preview"
          ? "Prompt 已按采集上限省略，仅显示预览"
          : detail.prompt_source === "decrypt_error"
            ? detail.decrypt_error || "完整 Prompt 解密失败，仅显示预览"
            : "仅显示预览";
    setPreview({
      title: item.request_id,
      text: detail.prompt_text || detail.prompt_preview || "无预览内容",
      meta: `${detail.user} / ${detail.model} / ${detail.token} / ${detail.time} / ${compact(detail.prompt_len)} chars / ${sourceNote}`,
    });
  } catch (err) {
    setPreview({
      title: item.request_id,
      text: "",
      meta: initialMeta,
      error: err instanceof Error ? err.message : "无法加载 Prompt 预览",
    });
  }
}

function RequestList({
  requests,
  navigate,
  setPreview,
}: {
  requests: RequestItem[];
  navigate: (to: string) => void;
  setPreview: (preview: PreviewState) => void;
}) {
  if (requests.length === 0) return <EmptyState label="暂无请求记录" />;
  return (
    <div className="mt-5">
      <div className="hidden lg:block">
        <div className="table-shell">
          <table>
            <thead>
              <tr>
                <th>时间 / 用户</th>
                <th>模型 / Token</th>
                <th>用量</th>
                <th>结论</th>
                <th>Prompt 预览</th>
              </tr>
            </thead>
            <tbody>
              {requests.map((item) => (
                <tr key={item.request_id}>
                  <td>
                    <div className="text-white">{item.time}</div>
                    <button className="mt-1 text-sm text-cyan-100 hover:text-cyan-50" onClick={() => navigate(`/admin/users/${encodeURIComponent(item.identity_key)}`)}>
                      {item.user}
                    </button>
                  </td>
                  <td>
                    <div className="font-medium text-white">{item.model}</div>
                    <div className="mt-1 text-sm text-slate-400">{String(item.token)}</div>
                  </td>
                  <td>
                    <div className="font-semibold text-white">{item.tokens_compact}</div>
                    <div className="text-xs text-slate-400">Quota {item.quota_compact}</div>
                  </td>
                  <td>
                    <StatusPill verdict={item.verdict} label={item.verdict_label} />
                    <div className="mt-2 text-xs text-slate-400">{item.category}</div>
                  </td>
                  <td>
                    <button
                      className="line-clamp-2 max-w-xl text-left text-sm leading-6 text-slate-300 hover:text-white"
                      onClick={() => void openPromptPreview(item, setPreview)}
                    >
                      {item.preview_short || "无预览内容"}
                    </button>
                    {item.reason && <div className="mt-2 line-clamp-1 text-xs text-slate-500">{item.reason}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="grid gap-3 lg:hidden">
        {requests.map((item) => (
          <div key={item.request_id} className="rounded-2xl border border-white/10 bg-white/[0.045] p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="text-sm text-slate-400">{item.time}</div>
                <button className="mt-1 truncate text-left font-medium text-white" onClick={() => navigate(`/admin/users/${encodeURIComponent(item.identity_key)}`)}>
                  {item.user}
                </button>
              </div>
              <StatusPill verdict={item.verdict} label={item.verdict_label} />
            </div>
            <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
              <div className="rounded-xl bg-white/[0.045] p-3">
                <div className="text-slate-400">模型</div>
                <div className="mt-1 truncate text-white">{item.model}</div>
              </div>
              <div className="rounded-xl bg-white/[0.045] p-3">
                <div className="text-slate-400">Tokens</div>
                <div className="mt-1 text-white">{item.tokens_compact}</div>
              </div>
            </div>
            <button
              className="mt-3 line-clamp-3 w-full rounded-2xl border border-white/10 bg-white/[0.045] p-3 text-left text-sm leading-6 text-slate-300"
              onClick={() => void openPromptPreview(item, setPreview)}
            >
              {item.preview_short || "无预览内容"}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

function DailyReportPage() {
  const [date, setDate] = useState(today());
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    apiFetch<{ url: string }>(`/admin/api/report-url?${new URLSearchParams({ date })}`)
      .then((payload) => {
        setUrl(payload.url);
        setError("");
      })
      .catch((err) => setError(err instanceof Error ? err.message : "无法加载日报"));
  }, [date]);

  return (
    <div className="space-y-5">
      <PageTitle eyebrow="Daily Report" title="Token 审计日报" subtitle="管理端可选择日期查看，企业微信 textcard 仍指向公开日报链接。" />
      <GlassCard>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <label className="block text-sm text-slate-300">
            日报日期
            <input className="glass-input mt-2 sm:w-64" type="date" value={date} onChange={(event) => setDate(event.target.value)} />
          </label>
          <a className="glass-button-muted" href={url || "#"} target="_blank" rel="noreferrer">
            <Eye className="h-4 w-4" />
            新窗口打开
          </a>
        </div>
      </GlassCard>
      {error && <ErrorBanner message={error} />}
      <div className="glass-card overflow-hidden p-0">
        {url ? <iframe className="h-[78vh] min-h-[620px] w-full border-0 bg-transparent" src={url} title="Token 审计日报" /> : <LoadingCard label="正在加载日报" />}
      </div>
    </div>
  );
}

function PageTitle({ eyebrow, title, subtitle, action }: { eyebrow: string; title: string; subtitle: string; action?: ReactNode }) {
  return (
    <div className="glass-card overflow-hidden p-6 sm:p-7">
      <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="mb-3 inline-flex rounded-full border border-white/15 bg-white/10 px-3 py-1 text-xs font-medium uppercase tracking-wider text-cyan-100">{eyebrow}</div>
          <h1 className="text-3xl font-semibold tracking-normal text-white sm:text-4xl">{title}</h1>
          <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-300 sm:text-base">{subtitle}</p>
        </div>
        {action && <div className="shrink-0">{action}</div>}
      </div>
    </div>
  );
}

function GlassCard({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`glass-card p-5 sm:p-6 ${className}`}>{children}</section>;
}

function SectionHeader({ icon: Icon, title, subtitle }: { icon: typeof Activity; title: string; subtitle?: string }) {
  return (
    <div className="flex items-start gap-3">
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl border border-white/15 bg-white/[0.08] text-cyan-100">
        <Icon className="h-5 w-5" />
      </div>
      <div className="min-w-0">
        <h2 className="font-semibold text-white">{title}</h2>
        {subtitle && <p className="mt-1 text-sm leading-6 text-slate-400">{subtitle}</p>}
      </div>
    </div>
  );
}

function MetricCard({
  icon: Icon,
  label,
  value,
  sub,
  tone,
}: {
  icon: typeof Activity;
  label: string;
  value: string;
  sub: string;
  tone: "cyan" | "violet" | "teal" | "rose";
}) {
  const toneClass = {
    cyan: "from-cyan-300/24 to-cyan-300/5 text-cyan-100",
    violet: "from-violet-300/24 to-violet-300/5 text-violet-100",
    teal: "from-teal-300/24 to-teal-300/5 text-teal-100",
    rose: "from-rose-300/24 to-rose-300/5 text-rose-100",
  }[tone];
  return (
    <div className="glass-card flex min-h-44 flex-col items-center justify-center p-5 text-center">
      <div className="flex flex-col items-center gap-3">
        <div className={`flex h-11 w-11 items-center justify-center rounded-2xl border border-white/15 bg-gradient-to-br ${toneClass}`}>
          <Icon className="h-5 w-5" />
        </div>
        <div className="text-xs uppercase tracking-wider text-slate-500">{label}</div>
      </div>
      <div className="mt-5 min-h-10 break-words text-3xl font-semibold leading-tight text-white">{value}</div>
      <div className="mt-2 text-sm text-slate-400">{sub}</div>
    </div>
  );
}

function StatusPill({ verdict, label }: { verdict: string; label: string }) {
  const classes =
    verdict === "work"
      ? "border-teal-200/25 bg-teal-300/10 text-teal-100"
      : verdict === "non_work"
        ? "border-rose-200/25 bg-rose-300/10 text-rose-100"
        : verdict === "uncertain"
          ? "border-amber-200/25 bg-amber-300/10 text-amber-100"
          : "border-slate-200/20 bg-slate-200/10 text-slate-200";
  return <span className={`status-pill ${classes}`}>{label}</span>;
}

function PromptModal({ preview, close }: { preview: PreviewState; close: () => void }) {
  if (!preview) return null;
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-950/70 px-4 py-8 backdrop-blur-sm" role="dialog" aria-modal="true">
      <div className="glass-card max-h-[86vh] w-full max-w-4xl overflow-hidden p-0">
        <div className="flex items-start justify-between gap-4 border-b border-white/10 p-5">
          <div className="min-w-0">
            <div className="truncate font-semibold text-white">{preview.title}</div>
            {preview.meta && <div className="mt-1 text-sm text-slate-400">{preview.meta}</div>}
          </div>
          <button className="glass-button-muted h-10 w-10 p-0" onClick={close} aria-label="关闭">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="max-h-[66vh] overflow-auto p-5">
          {preview.loading ? (
            <div className="flex items-center gap-3 text-sm text-slate-300">
              <RefreshCw className="h-4 w-4 animate-spin text-cyan-100" />
              正在解密完整 Prompt
            </div>
          ) : preview.error ? (
            <div className="rounded-2xl border border-rose-200/25 bg-rose-300/10 p-4 text-sm text-rose-100">{preview.error}</div>
          ) : (
            <div className="prompt-markdown">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{preview.text}</ReactMarkdown>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function PaginationControls({ pagination, setPage }: { pagination: Pagination; setPage: (page: number) => void }) {
  return (
    <div className="mt-5 flex flex-col gap-3 border-t border-white/10 pt-4 text-sm text-slate-300 sm:flex-row sm:items-center sm:justify-between">
      <div>
        共 {pagination.total} 条，第 {pagination.page} / {pagination.total_pages} 页
      </div>
      <div className="flex gap-2">
        <button className="glass-button-muted" disabled={!pagination.has_prev} onClick={() => setPage(Math.max(1, pagination.page - 1))}>
          <ChevronLeft className="h-4 w-4" />
          上一页
        </button>
        <button className="glass-button-muted" disabled={!pagination.has_next} onClick={() => setPage(pagination.page + 1)}>
          下一页
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

function LoadingCard({ label }: { label: string }) {
  return (
    <GlassCard>
      <div className="flex items-center gap-3 text-slate-300">
        <RefreshCw className="h-5 w-5 animate-spin text-cyan-100" />
        {label}
      </div>
    </GlassCard>
  );
}

function EmptyState({ label }: { label: string }) {
  return (
    <div className="mt-5 rounded-2xl border border-dashed border-white/15 bg-white/[0.03] p-8 text-center text-sm text-slate-400">
      <UserX className="mx-auto mb-3 h-6 w-6 text-slate-500" />
      {label}
    </div>
  );
}

function ErrorBanner({ message }: { message: string }) {
  return <div className="mt-5 rounded-2xl border border-rose-200/25 bg-rose-300/10 p-4 text-sm text-rose-100">{message}</div>;
}

async function apiFetch<T = unknown>(path: string, options: RequestInit = {}, csrfToken = ""): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const response = await fetch(path, {
    ...options,
    headers,
    credentials: "same-origin",
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      detail = typeof payload.detail === "string" ? payload.detail : detail;
    } catch {
      detail = await response.text();
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

function currentPath() {
  return window.location.pathname.replace(/\/$/, "") || "/admin";
}

function currentSearch() {
  return window.location.search;
}

function parseRequestFilters(search: string) {
  const params = new URLSearchParams(search);
  return {
    start: params.get("start") || "",
    end: params.get("end") || "",
    verdict: params.get("verdict") || "",
    user: params.get("user") || "",
    token: params.get("token") || "",
    model: params.get("model") || "",
    q: params.get("q") || "",
  };
}

function parseRequestPage(search: string) {
  const page = Number(new URLSearchParams(search).get("page") || 1);
  return Number.isFinite(page) && page > 0 ? Math.floor(page) : 1;
}

function isActiveNav(path: string, itemPath: string) {
  if (itemPath === "/admin/users") {
    return path === itemPath || path.startsWith("/admin/users/");
  }
  return path === itemPath;
}

function compact(value: number | string) {
  const numeric = Number(value || 0);
  if (numeric >= 1_000_000) return `${(numeric / 1_000_000).toFixed(numeric >= 10_000_000 ? 0 : 1)}M`;
  if (numeric >= 10_000) return `${Math.round(numeric / 1000)}K`;
  if (numeric >= 1000) return `${(numeric / 1000).toFixed(1)}K`;
  return String(numeric);
}

function today() {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

export default App;

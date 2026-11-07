import { useAuth } from "../lib/auth";
import { Card } from "../components/ui";

export function ProfilePage() {
  const { user } = useAuth();
  if (!user) return null;
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <h1 className="text-2xl font-semibold text-forest-900">My profile</h1>
      <Card className="divide-y divide-ivory-200">
        {[
          ["Name", user.full_name],
          ["Email", user.email],
          ["Role", user.role],
        ].map(([k, v]) => (
          <div key={k} className="grid grid-cols-3 gap-4 px-4 py-3 text-sm">
            <dt className="text-slate-500">{k}</dt>
            <dd className="col-span-2 text-slate-900">{v}</dd>
          </div>
        ))}
      </Card>
      <Card className="p-4">
        <h2 className="mb-2 text-sm font-semibold text-slate-800">Your permissions ({user.permissions.length})</h2>
        <div className="flex flex-wrap gap-1.5">
          {user.permissions.map((p) => (
            <code key={p} className="rounded bg-ivory-200 px-1.5 py-0.5 text-xs text-slate-700">
              {p}
            </code>
          ))}
        </div>
        <p className="mt-3 text-xs text-slate-500">The server enforces these permissions on every request; the menu only reflects them.</p>
      </Card>
    </div>
  );
}

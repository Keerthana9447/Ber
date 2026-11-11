import { Link } from "react-router-dom";
import { Card } from "../components/ui";

export function NotFoundPage() {
  return (
    <Card className="mx-auto mt-10 max-w-md p-8 text-center">
      <p className="text-3xl font-semibold text-forest-800">404</p>
      <p className="mt-2 text-sm text-slate-600">This page does not exist.</p>
      <Link to="/" className="mt-4 inline-block text-sm font-medium text-forest-700 underline">
        Back to dashboard
      </Link>
    </Card>
  );
}

export function ForbiddenPage() {
  return (
    <Card className="mx-auto mt-10 max-w-md p-8 text-center">
      <p className="text-3xl font-semibold text-forest-800">403</p>
      <p className="mt-2 text-sm text-slate-600">Your role does not have access to this page.</p>
      <Link to="/" className="mt-4 inline-block text-sm font-medium text-forest-700 underline">
        Back to dashboard
      </Link>
    </Card>
  );
}

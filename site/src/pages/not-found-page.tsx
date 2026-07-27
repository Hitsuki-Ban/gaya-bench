import { Link } from "react-router";

import { Button } from "@/components/ui/button";

export function NotFoundPage() {
  return (
    <div className="grid min-h-[60vh] place-items-center text-center">
      <div>
        <p className="font-mono text-sm text-primary">404 / NOT FOUND</p>
        <h1 className="mt-4 text-3xl font-semibold">ページが見つかりません</h1>
        <p className="mt-3 text-sm text-muted-foreground">URL または対象 ID を確認してください。</p>
        <Button className="mt-6" nativeButton={false} render={<Link to="/" />}>
          比較画面へ戻る
        </Button>
      </div>
    </div>
  );
}

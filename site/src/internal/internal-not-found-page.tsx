import { Link } from "react-router";

import { Button } from "@/components/ui/button";

export function InternalNotFoundPage() {
  return (
    <div className="grid min-h-[60vh] place-items-center text-center">
      <div>
        <p className="font-mono text-sm text-primary">404 / LOCAL TOOL</p>
        <h1 className="mt-4 text-3xl font-semibold">評価画面が見つかりません</h1>
        <p className="mt-3 text-sm text-muted-foreground">
          音声選定または事前確認を選んでください。
        </p>
        <Button className="mt-6" nativeButton={false} render={<Link to="/curate" />}>
          音声選定へ
        </Button>
      </div>
    </div>
  );
}

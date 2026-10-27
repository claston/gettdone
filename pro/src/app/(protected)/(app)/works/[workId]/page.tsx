import { StatementWorks } from "@/components/works/statement-works";
export default async function Page({
  params,
}: {
  params: Promise<{ workId: string }>;
}) {
  const { workId } = await params;
  return <StatementWorks initialId={workId} />;
}

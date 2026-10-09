import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { KnowledgeDocument } from "@/lib/knowledge-types";

import { renderWithIntl } from "../test-utils";
import { DocumentList } from "./document-list";

function renderList(documents: KnowledgeDocument[]) {
  return renderWithIntl(
    <DocumentList
      documents={documents}
      isLoading={false}
      error={undefined}
      onRefresh={vi.fn()}
    />,
  );
}

describe("DocumentList", () => {
  it("renders a completed document with the contract's ready wording", () => {
    renderList([{ id: "doc-1", name: "员工手册.pdf", status: "completed" }]);

    expect(screen.getByText("就绪")).toBeTruthy();
    expect(screen.queryByText("已完成")).toBeNull();
  });

  it("shows the indexing-failure copy on a failed document", () => {
    renderList([{ id: "doc-2", name: "损坏文件.pdf", status: "failed" }]);

    expect(screen.getByText("索引失败：文件解析错误")).toBeTruthy();
  });

  it("does not show the indexing-failure copy while a document is still indexing", () => {
    renderList([{ id: "doc-3", name: "解析中.pdf", status: "indexing" }]);

    expect(screen.getByText("索引中")).toBeTruthy();
    expect(screen.queryByText("索引失败：文件解析错误")).toBeNull();
  });
});

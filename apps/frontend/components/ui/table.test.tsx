import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DataTable } from "./table";

interface Row {
  id: string;
  name: string;
}

const COLUMNS = [{ key: "name", header: "名称" }];

const ROWS: Row[] = [
  { id: "1", name: "A" },
  { id: "2", name: "B" },
  { id: "3", name: "C" },
];

describe("DataTable", () => {
  it("normalizes a non-positive pageSize to 10", () => {
    render(
      <DataTable
        columns={COLUMNS}
        data={ROWS}
        rowKey={(row) => row.id}
        pageSize={0}
      />,
    );
    // header row + all three data rows render on a single page
    expect(screen.getAllByRole("row")).toHaveLength(4);
  });

  it("paginates rows by pageSize", () => {
    render(
      <DataTable
        columns={COLUMNS}
        data={ROWS}
        rowKey={(row) => row.id}
        pageSize={2}
      />,
    );
    // header + 2 data rows on the first page
    expect(screen.getAllByRole("row")).toHaveLength(3);
  });
});

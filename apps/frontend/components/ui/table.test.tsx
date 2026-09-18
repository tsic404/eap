import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

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

function stubMatchMedia(matches: boolean) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

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

  it("renders cards instead of the table on mobile", async () => {
    stubMatchMedia(true);
    render(<DataTable columns={COLUMNS} data={ROWS} rowKey={(row) => row.id} />);

    await waitFor(() => {
      expect(screen.queryByRole("table")).toBeNull();
    });
    // Each row becomes a card with the column header as its label.
    expect(screen.getAllByText("名称")).toHaveLength(3);
  });
});

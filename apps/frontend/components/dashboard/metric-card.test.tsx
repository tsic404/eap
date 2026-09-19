import { render, screen } from "@testing-library/react";
import { Bot } from "lucide-react";
import { describe, expect, it } from "vitest";

import { MetricCard } from "./metric-card";

describe("MetricCard", () => {
  it("renders the label, value, and hint", () => {
    render(<MetricCard label="智能体总数" value="42" icon={Bot} hint="今日" />);

    expect(screen.getByText("智能体总数")).toBeTruthy();
    expect(screen.getByText("42")).toBeTruthy();
    expect(screen.getByText("今日")).toBeTruthy();
  });
});

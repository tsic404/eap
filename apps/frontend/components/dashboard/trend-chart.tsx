"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { TrendPoint } from "@/lib/dashboard-types";

export interface TrendChartProps {
  points: TrendPoint[];
}

/** ISO hour → `HH:00` axis label; falls back to the raw value on parse failure. */
function hourLabel(hour: string): string {
  const date = new Date(hour);
  if (Number.isNaN(date.getTime())) return hour;
  return `${String(date.getHours()).padStart(2, "0")}:00`;
}

/** 24h call-volume trend as a recharts line chart. */
export function TrendChart({ points }: TrendChartProps) {
  const data = points.map((point) => ({ ...point, label: hourLabel(point.hour) }));

  return (
    <Card className="flex h-full flex-col">
      <CardHeader>
        <CardTitle>24 小时调用趋势</CardTitle>
      </CardHeader>
      <CardContent className="h-64 flex-1">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart
            data={data}
            margin={{ top: 4, right: 8, bottom: 0, left: -20 }}
          >
            <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 12 }}
              interval="preserveStartEnd"
            />
            <YAxis allowDecimals={false} tick={{ fontSize: 12 }} />
            <Tooltip />
            <Line
              type="monotone"
              dataKey="calls"
              stroke="var(--color-primary)"
              strokeWidth={2}
              dot={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}

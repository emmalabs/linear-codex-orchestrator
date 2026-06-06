import * as React from "react";
import { Anchor, Badge, Card, Stack, Text } from "@mantine/core";
import { statusTone } from "../lib/format";

export function MetricCard(props: { label: string; value: string | number; detail: string }) {
  return (
    <Card withBorder padding="sm">
      <Text c="dimmed" fw={700} size="xs" tt="uppercase">{props.label}</Text>
      <Text fw={750} size="xl" lh={1.2}>{props.value}</Text>
      <Text c="dimmed" className="truncate" size="sm">{props.detail}</Text>
    </Card>
  );
}

export function StatusPill({ status }: { status?: string }) {
  const tone = statusTone(status);
  const color = tone === "success" ? "green" : tone === "error" ? "red" : tone === "active" ? "blue" : "gray";
  return (
    <Badge
      className={`status-pill status-pill-${tone}`}
      color={color}
      radius="xl"
      size="sm"
      variant="light"
    >
      <span className="status-pill-dot" aria-hidden="true" />
      {status || "Unknown"}
    </Badge>
  );
}

export function SummaryList(props: { emptyText: string; children: React.ReactNode }) {
  const children = React.Children.toArray(props.children);
  if (!children.length) {
    return <Text c="dimmed" p="sm" size="sm">{props.emptyText}</Text>;
  }
  return <Stack gap={0}>{children}</Stack>;
}

export function ExternalLink({ href, children }: { href?: string; children: React.ReactNode }) {
  if (!href) {
    return <span>{children}</span>;
  }
  return (
    <Anchor
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      fw={700}
      onClick={(event) => event.stopPropagation()}
    >
      {children}
    </Anchor>
  );
}

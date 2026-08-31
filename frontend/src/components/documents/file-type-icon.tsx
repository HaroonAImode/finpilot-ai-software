/**
 * A file's type, represented the way a real file manager does: a
 * consistent icon + color per kind, not a blank generic page icon for
 * everything. Colors are FinPilot's own categorical chart palette
 * (--chart-1..5, the same tokens every chart on this app already uses for
 * "these are different categories") — not a new palette invented for this
 * one page.
 */
import {
  File, FileArchive, FileAudio, FileImage, FileSpreadsheet, FileText, FileVideo, type LucideIcon,
} from "lucide-react";
import { type FileKind } from "@/lib/documents";

const FILE_KIND_META: Record<FileKind, { icon: LucideIcon; color: string }> = {
  pdf: { icon: FileText, color: "var(--chart-1)" },
  word: { icon: FileText, color: "var(--chart-2)" },
  excel: { icon: FileSpreadsheet, color: "var(--chart-3)" },
  image: { icon: FileImage, color: "var(--chart-4)" },
  archive: { icon: FileArchive, color: "var(--chart-5)" },
  audio: { icon: FileAudio, color: "var(--chart-2)" },
  video: { icon: FileVideo, color: "var(--chart-1)" },
  text: { icon: FileText, color: "var(--muted-foreground)" },
  other: { icon: File, color: "var(--muted-foreground)" },
};

export function FileTypeIcon({ kind, size = "sm" }: { kind: FileKind; size?: "sm" | "lg" }) {
  const { icon: Icon, color } = FILE_KIND_META[kind];
  const box = size === "lg" ? "h-14 w-14" : "h-9 w-9";
  const iconSize = size === "lg" ? "h-7 w-7" : "h-4.5 w-4.5";

  return (
    <span
      className={`grid ${box} shrink-0 place-items-center rounded-xl`}
      style={{ backgroundColor: `color-mix(in oklch, ${color} 16%, transparent)`, color }}
    >
      <Icon className={iconSize} />
    </span>
  );
}

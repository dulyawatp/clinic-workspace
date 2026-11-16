import type { ReactNode } from "react";

export type TableColumn = {
  key: string;
  label: string;
  align?: "start" | "end";
};

type DataTableProps = {
  caption?: string;
  columns: readonly TableColumn[];
  /** One array of cells per row, in `columns` order. */
  rows: readonly (readonly ReactNode[])[];
  /** Shown inside the table when there are no rows. */
  empty?: string;
};

/**
 * Hairline clinical table. The horizontal scroll wrapper is not optional: a
 * dense table must scroll rather than clip records on narrow viewports.
 */
export function DataTable({ caption, columns, rows, empty }: DataTableProps) {
  return (
    <div className="pc-tablewrap">
      <table className="pc-data">
        {caption && <caption>{caption}</caption>}
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className={column.align === "end" ? "pc-num" : undefined}
              >
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td className="pc-table-empty" colSpan={columns.length}>
                {empty}
              </td>
            </tr>
          ) : (
            rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {row.map((cell, cellIndex) => {
                  const column = columns[cellIndex];
                  return (
                    <td
                      key={column?.key ?? cellIndex}
                      data-label={column?.label}
                      className={
                        column?.align === "end" ? "pc-num" : undefined
                      }
                    >
                      {cell}
                    </td>
                  );
                })}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

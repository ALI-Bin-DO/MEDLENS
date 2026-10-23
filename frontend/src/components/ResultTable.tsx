// Rows are shown exactly as returned: positional columns (duplicate names are fine), exact
// decimal strings, and NULL kept visibly different from an empty string.
import type { Cell } from "../services/types";

const NUMERIC = /^-?\d+(\.\d+)?$/;

function isNumericColumn(rows: Cell[][], index: number): boolean {
  let seen = false;
  for (const row of rows) {
    const value = row[index];
    if (value === null || value === undefined) continue;
    if (typeof value === "number" || (typeof value === "string" && NUMERIC.test(value))) seen = true;
    else return false;
  }
  return seen;
}

export function CellValue({ value }: { value: Cell | undefined }) {
  if (value === null || value === undefined) return <span className="cell-null">NULL</span>;
  if (value === "") return <span className="cell-empty">(empty text)</span>;
  return <>{String(value)}</>;
}

export function ResultTable({ columns, rows }: { columns: string[]; rows: Cell[][] }) {
  const numeric = columns.map((_, index) => isNumericColumn(rows, index));
  return (
    <div className="table-scroll" role="region" aria-label="Query result" tabIndex={0}>
      <table className="result-table">
        <thead>
          <tr>
            {columns.map((column, index) => (
              <th key={index} scope="col" className={numeric[index] ? "num" : undefined}>
                {column === "" ? <span className="cell-empty">(unnamed)</span> : column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {columns.map((_, index) => (
                <td key={index} className={numeric[index] ? "num" : undefined}><CellValue value={row[index]} /></td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

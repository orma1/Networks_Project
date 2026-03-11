import React from "react";
// Make sure to import DnsRecord type if it's not globally available without import

interface ZoneTableProps {
  records: DnsRecord[];
  onUpdateRecord: UpdateRecordFn;
  onDeleteRecord: (id: string) => void;
}

export default function ZoneTable({
  records,
  onUpdateRecord,
  onDeleteRecord,
}: ZoneTableProps) {
  if (!records || records.length === 0) return null;

  return (
    <div className="table-container">
      <table className="zone-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Class</th>
            <th>Type</th>
            <th>TTL (Opt)</th>
            <th>Target / Data</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {records.map((record) => (
            <tr key={record.id}>
              <td>
                <input
                  type="text"
                  value={record.name}
                  onChange={(e) =>
                    onUpdateRecord(record.id, "name", e.target.value)
                  }
                />
              </td>
              <td>
                <select
                  value={record.class}
                  onChange={(e) =>
                    onUpdateRecord(record.id, "class", e.target.value)
                  }
                >
                  <option value="IN">IN</option>
                  <option value="CH">CH</option>
                  <option value="HS">HS</option>
                </select>
              </td>
              <td>
                <select
                  value={record.type}
                  onChange={(e) =>
                    onUpdateRecord(record.id, "type", e.target.value)
                  }
                >
                  <option value="SOA">SOA</option>
                  <option value="NS">NS</option>
                  <option value="A">A</option>
                  <option value="CNAME">CNAME</option>
                  <option value="TXT">TXT</option>
                </select>
              </td>
              <td>
                <input
                  type="number"
                  value={record.ttl || ""}
                  onChange={(e) =>
                    onUpdateRecord(
                      record.id,
                      "ttl",
                      e.target.value ? parseInt(e.target.value) : undefined,
                    )
                  }
                  placeholder="Default"
                />
              </td>
              <td>
                <input
                  type="text"
                  value={record.data}
                  onChange={(e) =>
                    onUpdateRecord(record.id, "data", e.target.value)
                  }
                />
              </td>
              <td>
                <button
                  className="btn-delete"
                  onClick={() => onDeleteRecord(record.id)}
                >
                  Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

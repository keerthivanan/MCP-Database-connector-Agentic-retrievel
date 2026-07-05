import TracePanel from "./TracePanel.jsx";

export default function MessageBubble({ msg }) {
  return (
    <div className={`msg ${msg.role}`}>
      {msg.trace && msg.trace.length > 0 && <TracePanel trace={msg.trace} />}
      <div className="bubble">{msg.content}</div>
    </div>
  );
}

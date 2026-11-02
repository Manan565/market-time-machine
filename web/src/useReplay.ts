import { useCallback, useEffect, useRef, useState } from "react";
import { Frame, replaySocketUrl } from "./api";

export type ReplayStatus = "connecting" | "open" | "closed";

/** Owns the replay websocket for one symbol and day, and exposes playback controls. */
export function useReplay(symbol: string | null, date: string | null) {
  const socket = useRef<WebSocket | null>(null);
  const speedRef = useRef(600);
  const [frame, setFrame] = useState<Frame | null>(null);
  const [offset, setOffset] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeedState] = useState(600);
  const [status, setStatus] = useState<ReplayStatus>("connecting");

  useEffect(() => {
    if (!symbol || !date) return;
    setFrame(null);
    setOffset(0);
    setPlaying(false);
    setStatus("connecting");
    const sock = new WebSocket(replaySocketUrl(symbol, date));
    socket.current = sock;
    sock.onopen = () => {
      setStatus("open");
      sock.send(JSON.stringify({ type: "speed", speed: speedRef.current }));
    };
    sock.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.type === "frame" || msg.type === "snapshot") {
        setFrame(msg as Frame);
        setOffset(msg.offset_s);
      } else if (msg.type === "end") {
        setPlaying(false);
      }
    };
    sock.onclose = () => setStatus("closed");
    return () => {
      sock.onclose = null;
      sock.close();
    };
  }, [symbol, date]);

  const send = useCallback((payload: object) => {
    if (socket.current?.readyState === WebSocket.OPEN) socket.current.send(JSON.stringify(payload));
  }, []);

  const play = useCallback(() => {
    send({ type: "play", speed: speedRef.current });
    setPlaying(true);
  }, [send]);
  const pause = useCallback(() => {
    send({ type: "pause" });
    setPlaying(false);
  }, [send]);
  const seek = useCallback(
    (offsetS: number) => {
      setOffset(offsetS);
      send({ type: "seek", offset_s: offsetS });
    },
    [send],
  );
  const setSpeed = useCallback(
    (value: number) => {
      speedRef.current = value;
      setSpeedState(value);
      send({ type: "speed", speed: value });
    },
    [send],
  );

  return { frame, offset, playing, speed, status, play, pause, seek, setSpeed };
}

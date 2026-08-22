import { useEffect, useState } from "react";

// Cycles through a fixed list of copy while `active` is true - purely a
// pacing device for a single indeterminate request (no fake percentages,
// no invented progress). Resets to the first message whenever a new
// operation starts.
export function useCyclingMessage(messages: string[], active: boolean, intervalMs = 1400): string {
  const [index, setIndex] = useState(0);

  useEffect(() => {
    if (!active) {
      setIndex(0);
      return;
    }
    const timer = setInterval(() => {
      setIndex((i) => (i + 1) % messages.length);
    }, intervalMs);
    return () => clearInterval(timer);
  }, [active, messages, intervalMs]);

  return messages[index];
}

import { useEffect, useRef, useState } from 'react';
import { api } from '../api';

const POLL_INTERVAL_MS = 1500;
const MAX_POLL_MINUTES = 15;

/**
 * Polls a job until it completes or fails.
 *
 * Replaces the previous pattern of re-creating a setInterval on every
 * `activeJob` state update (finding L4), which caused interval drift and
 * bursts. Uses self-scheduling setTimeout with cancellation and a hard
 * timeout so abandoned polls don't run forever.
 */
export function useJobPolling(onFinished) {
  const [activeJob, setActiveJob] = useState(null);
  const timerRef = useRef(null);
  const cancelledRef = useRef(true);
  const startedAtRef = useRef(0);
  const activeJobRef = useRef(null);
  const onFinishedRef = useRef(onFinished);

  // Keep callbacks/objects in sync without re-triggering the polling effect
  useEffect(() => {
    onFinishedRef.current = onFinished;
  }, [onFinished]);

  useEffect(() => {
    if (!activeJob) return;
    activeJobRef.current = activeJob;

    if (activeJob.status === 'completed' || activeJob.status === 'failed') return;

    cancelledRef.current = false;
    startedAtRef.current = Date.now();

    const stop = () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };

    const tick = async () => {
      if (cancelledRef.current) return;

      const job = activeJobRef.current;
      if (!job) return;

      if (Date.now() - startedAtRef.current > MAX_POLL_MINUTES * 60 * 1000) {
        stop();
        onFinishedRef.current?.({
          ...job,
          status: 'failed',
          error_message: 'Processing is taking unusually long. Check your Projects page later.',
        });
        return;
      }

      try {
        const res = await api.jobs.get(job.id);
        if (cancelledRef.current) return;
        activeJobRef.current = res;
        setActiveJob(res);

        if (res.status === 'completed' || res.status === 'failed') {
          stop();
          onFinishedRef.current?.(res);
          return; // no reschedule
        }
      } catch (err) {
        console.error(err);
      }

      timerRef.current = setTimeout(tick, POLL_INTERVAL_MS);
    };

    timerRef.current = setTimeout(tick, POLL_INTERVAL_MS);

    return () => {
      cancelledRef.current = true;
      stop();
    };
  }, [activeJob?.id]);

  const startPolling = (job) => {
    setActiveJob(job);
  };

  const stopPolling = () => {
    cancelledRef.current = true;
    if (timerRef.current) clearTimeout(timerRef.current);
  };

  return { activeJob, startPolling, stopPolling, setActiveJob };
}

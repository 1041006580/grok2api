(function (global) {
  function openBatchStream(taskId, apiKey, handlers = {}) {
    if (!taskId) return null;
    // 去掉 Bearer 前缀，后端期望纯 api_key
    const cleanKey = apiKey ? apiKey.replace(/^Bearer\s+/i, '') : '';
    const url = `/api/v1/admin/batch/${taskId}/stream?api_key=${encodeURIComponent(cleanKey)}`;
    const es = new EventSource(url);

    es.onmessage = (e) => {
      if (!e.data) return;
      let msg;
      try {
        msg = JSON.parse(e.data);
      } catch {
        return;
      }
      if (handlers.onMessage) handlers.onMessage(msg);
    };

    es.onerror = () => {
      if (handlers.onError) handlers.onError();
    };

    return es;
  }

  function closeBatchStream(es) {
    if (es) es.close();
  }

  async function cancelBatchTask(taskId, apiKey) {
    if (!taskId) return;
    try {
      await fetch(`/api/v1/admin/batch/${taskId}/cancel`, {
        method: 'POST',
        headers: apiKey ? { Authorization: `Bearer ${apiKey}` } : undefined
      });
    } catch {
      // ignore
    }
  }

  global.BatchSSE = {
    open: openBatchStream,
    close: closeBatchStream,
    cancel: cancelBatchTask
  };
})(window);

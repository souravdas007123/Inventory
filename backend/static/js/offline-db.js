// Symaso: the small on-device store used by offline billing (shared by the page and the service worker).
// Two stores: "queue" (bills waiting to be sent) and "meta" (the medicine list, the signed-in user, recently sent bills).
(function (g) {
  const NAME = 'medsync-offline', VERSION = 1;
  function open() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(NAME, VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains('queue')) db.createObjectStore('queue', {keyPath: 'id'});
        if (!db.objectStoreNames.contains('meta')) db.createObjectStore('meta', {keyPath: 'key'});
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }
  async function run(store, mode, fn) {
    const db = await open();
    try {
      return await new Promise((resolve, reject) => {
        const tx = db.transaction(store, mode), out = fn(tx.objectStore(store));
        tx.oncomplete = () => resolve(out && 'result' in out ? out.result : undefined);
        tx.onerror = tx.onabort = () => reject(tx.error);
      });
    } finally { db.close(); }
  }
  g.MSDB = {
    put: (store, value) => run(store, 'readwrite', s => s.put(value)),
    get: (store, key) => run(store, 'readonly', s => s.get(key)),
    del: (store, key) => run(store, 'readwrite', s => s.delete(key)),
    all: store => run(store, 'readonly', s => s.getAll()),
    clear: store => run(store, 'readwrite', s => s.clear()),
  };
})(self);

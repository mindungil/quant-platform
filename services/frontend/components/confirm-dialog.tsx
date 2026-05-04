"use client";
import { motion, AnimatePresence } from "framer-motion";

interface Props {
  open: boolean;
  title: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({ open, title, message, confirmText = "확인", cancelText = "취소", danger, onConfirm, onCancel }: Props) {
  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
          onClick={onCancel}
        >
          <motion.div
            initial={{ scale: 0.95, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.95, opacity: 0 }}
            onClick={e => e.stopPropagation()}
            className="w-full max-w-sm rounded-xl border border-[#2e2e2e] bg-zinc-900 p-6 shadow-2xl"
          >
            <h3 className="text-lg font-semibold text-white">{title}</h3>
            <p className="mt-2 text-sm text-[#a1a1a1]">{message}</p>
            <div className="mt-6 flex justify-end gap-3">
              <button onClick={onCancel} className="rounded-lg px-4 py-2 text-sm text-[#a1a1a1] hover:bg-[#1c1c21]">
                {cancelText}
              </button>
              <button
                onClick={onConfirm}
                className={`rounded-lg px-4 py-2 text-sm font-medium ${
                  danger ? "bg-red-500 text-white hover:bg-red-400" : "bg-white text-black hover:bg-zinc-200"
                }`}
              >
                {confirmText}
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

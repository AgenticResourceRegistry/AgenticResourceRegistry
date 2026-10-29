// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useRef, useState } from "react";
import { Camera, Loader2, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { auth, setUserAvatar } from "@/lib/api";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";

const ALLOWED_TYPES = ["image/png", "image/jpeg", "image/webp"];
// Match server-side _MAX_AVATAR_BYTES (2MB). Canvas always crops to 256x256 PNG
// so the upload payload will be well under this, but guard the raw file too.
const MAX_FILE_SIZE = 2 * 1024 * 1024;
const MIN_OVERLAY = 80;

interface Overlay {
	x: number;
	y: number;
	size: number;
}

interface AvatarEditableProps {
	name: string;
	avatarUrl: string | null;
}

function initials(name: string) {
	return name
		.split(" ")
		.map((w) => w[0])
		.join("")
		.toUpperCase()
		.slice(0, 2);
}

export function AvatarEditable({ name, avatarUrl }: AvatarEditableProps) {
	const [open, setOpen] = useState(false);
	const [imageUrl, setImageUrl] = useState<string | null>(null);
	const [overlay, setOverlay] = useState<Overlay>({ x: 0, y: 0, size: 200 });
	const [uploading, setUploading] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const [imgDimensions, setImgDimensions] = useState({ w: 0, h: 0 });

	const imgRef = useRef<HTMLImageElement>(null);
	const fileInputRef = useRef<HTMLInputElement>(null);
	const dragRef = useRef<{
		type: "move" | "resize";
		startX: number;
		startY: number;
		startOverlay: Overlay;
	} | null>(null);

	const reset = () => {
		setImageUrl(null);
		setOverlay({ x: 0, y: 0, size: 200 });
		setImgDimensions({ w: 0, h: 0 });
		setError(null);
		if (fileInputRef.current) fileInputRef.current.value = "";
	};

	const handleOpenChange = (value: boolean) => {
		if (!value) reset();
		setOpen(value);
	};

	const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
		const file = e.target.files?.[0];
		if (!file) return;

		if (!ALLOWED_TYPES.includes(file.type)) {
			setError("Please upload a PNG, JPEG, or WebP image");
			return;
		}
		if (file.size > MAX_FILE_SIZE) {
			setError("Image must be under 2MB");
			return;
		}

		// Use FileReader instead of URL.createObjectURL so the preview src is
		// a browser-produced data: URL — not a user-controlled blob: URL.
		const reader = new FileReader();
		reader.onload = (ev) => {
			const result = ev.target?.result;
			if (typeof result === "string") {
				setImageUrl(result);
				setError(null);
			}
		};
		reader.readAsDataURL(file);
	};

	const handleImageLoad = () => {
		const img = imgRef.current;
		if (!img) return;

		const w = img.clientWidth;
		const h = img.clientHeight;
		setImgDimensions({ w, h });

		const size = Math.min(Math.floor(Math.min(w, h) * 0.6), 300);
		setOverlay({
			x: Math.floor((w - size) / 2),
			y: Math.floor((h - size) / 2),
			size,
		});
	};

	const handleMouseDown = useCallback(
		(e: React.MouseEvent, type: "move" | "resize") => {
			e.preventDefault();
			e.stopPropagation();
			dragRef.current = {
				type,
				startX: e.clientX,
				startY: e.clientY,
				startOverlay: { ...overlay },
			};

			const handleMouseMove = (me: MouseEvent) => {
				if (!dragRef.current) return;
				const dx = me.clientX - dragRef.current.startX;
				const dy = me.clientY - dragRef.current.startY;
				const start = dragRef.current.startOverlay;

				if (dragRef.current.type === "move") {
					let newX = start.x + dx;
					let newY = start.y + dy;
					newX = Math.max(0, Math.min(newX, imgDimensions.w - start.size));
					newY = Math.max(0, Math.min(newY, imgDimensions.h - start.size));
					setOverlay({ x: newX, y: newY, size: start.size });
				} else {
					const delta = Math.max(dx, dy);
					let newSize = start.size + delta;
					newSize = Math.max(MIN_OVERLAY, newSize);
					newSize = Math.min(
						newSize,
						imgDimensions.w - start.x,
						imgDimensions.h - start.y,
					);
					setOverlay({ x: start.x, y: start.y, size: newSize });
				}
			};

			const handleMouseUp = () => {
				dragRef.current = null;
				document.removeEventListener("mousemove", handleMouseMove);
				document.removeEventListener("mouseup", handleMouseUp);
			};

			document.addEventListener("mousemove", handleMouseMove);
			document.addEventListener("mouseup", handleMouseUp);
		},
		[overlay, imgDimensions],
	);

	const handleCropAndSave = async () => {
		const img = imgRef.current;
		if (!img || !imageUrl) return;

		setUploading(true);
		try {
			const scaleX = img.naturalWidth / img.clientWidth;
			const scaleY = img.naturalHeight / img.clientHeight;

			const sx = overlay.x * scaleX;
			const sy = overlay.y * scaleY;
			const sSize = overlay.size * Math.min(scaleX, scaleY);

			const canvas = document.createElement("canvas");
			canvas.width = 256;
			canvas.height = 256;
			const ctx = canvas.getContext("2d");
			if (!ctx) throw new Error("Canvas not supported");

			ctx.drawImage(img, sx, sy, sSize, sSize, 0, 0, 256, 256);
			const dataUrl = canvas.toDataURL("image/png");

			await auth.uploadAvatar({ avatar_url: dataUrl });
			setUserAvatar(dataUrl);

			reset();
			setOpen(false);
			toast.success("Profile picture updated");
		} catch (err) {
			toast.error(err instanceof Error ? err.message : "Upload failed");
		} finally {
			setUploading(false);
		}
	};

	const handleRemoveAvatar = async () => {
		setUploading(true);
		try {
			await auth.deleteAvatar();
			setUserAvatar(null);
			toast.success("Profile picture removed");
			setOpen(false);
		} catch (err) {
			toast.error(
				err instanceof Error ? err.message : "Failed to remove avatar",
			);
		} finally {
			setUploading(false);
		}
	};

	return (
		<>
			{/* Clickable avatar */}
			<button
				type="button"
				onClick={() => setOpen(true)}
				className="relative group shrink-0 rounded-full focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
			>
				<Avatar className="h-12 w-12" key={avatarUrl || "no-avatar"}>
					{avatarUrl && <AvatarImage src={avatarUrl} alt={name} />}
					<AvatarFallback className="text-sm font-semibold">
						{initials(name || "U")}
					</AvatarFallback>
				</Avatar>
				<div className="absolute inset-0 rounded-full bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
					<Camera className="h-4 w-4 text-white" />
				</div>
			</button>

			{/* Edit dialog */}
			<Dialog open={open} onOpenChange={handleOpenChange}>
				<DialogContent className="sm:max-w-lg">
					<DialogHeader>
						<DialogTitle>Edit Profile Picture</DialogTitle>
					</DialogHeader>

					<div className="space-y-4">
						{!imageUrl ? (
							<>
								<div
									className="border-2 border-dashed border-border rounded-lg p-8 text-center hover:bg-accent/5 transition-colors cursor-pointer"
									onClick={() => fileInputRef.current?.click()}
									onDragOver={(e) => e.preventDefault()}
									onDrop={(e) => {
										e.preventDefault();
										const file = e.dataTransfer.files?.[0];
										if (file) {
											const syntheticEvent = {
												target: { files: e.dataTransfer.files },
											} as unknown as React.ChangeEvent<HTMLInputElement>;
											handleFileChange(syntheticEvent);
										}
									}}
								>
									<Camera className="mx-auto h-6 w-6 text-muted-foreground mb-2" />
									<p className="text-sm font-medium">
										Click to choose or drop an image
									</p>
									<p className="text-xs text-muted-foreground mt-1">
										PNG, JPEG, or WebP (max 5MB)
									</p>
								</div>

								<input
									ref={fileInputRef}
									type="file"
									accept="image/png,image/jpeg,image/webp"
									onChange={handleFileChange}
									className="hidden"
								/>

								{error && <p className="text-sm text-destructive">{error}</p>}

								{avatarUrl && (
									<Button
										variant="outline"
										size="sm"
										onClick={handleRemoveAvatar}
										disabled={uploading}
										className="w-full"
									>
										{uploading ? (
											<Loader2 className="mr-2 h-4 w-4 animate-spin" />
										) : (
											<Trash2 className="mr-2 h-4 w-4" />
										)}
										Remove Picture
									</Button>
								)}
							</>
						) : (
							<div className="space-y-3">
								<p className="text-xs text-muted-foreground">
									Drag the box to position. Drag the corner to resize.
								</p>

								<div
									className="relative inline-block select-none overflow-hidden rounded-lg border border-border"
									style={{ maxWidth: "100%" }}
								>
									<img
										ref={imgRef}
										src={imageUrl ?? undefined}
										alt="Upload preview"
										onLoad={handleImageLoad}
										className="block max-w-full max-h-[350px] w-auto h-auto"
										draggable={false}
									/>

									{imgDimensions.w > 0 && (
										<>
											<div className="absolute inset-0 pointer-events-none">
												<div
													style={{
														position: "absolute",
														top: overlay.y,
														left: overlay.x,
														width: overlay.size,
														height: overlay.size,
														boxShadow: "0 0 0 9999px rgba(0,0,0,0.5)",
													}}
												/>
											</div>

											<div
												className="absolute border-2 border-white/90 rounded-sm cursor-move"
												style={{
													top: overlay.y,
													left: overlay.x,
													width: overlay.size,
													height: overlay.size,
												}}
												onMouseDown={(e) => handleMouseDown(e, "move")}
											>
												<div
													className="absolute bottom-0 right-0 w-4 h-4 bg-white border border-gray-400 rounded-sm cursor-nwse-resize translate-x-1/2 translate-y-1/2"
													onMouseDown={(e) => handleMouseDown(e, "resize")}
												/>
											</div>
										</>
									)}
								</div>

								<div className="flex gap-2">
									<Button
										onClick={handleCropAndSave}
										disabled={uploading || imgDimensions.w === 0}
										className="flex-1"
									>
										{uploading ? (
											<>
												<Loader2 className="mr-2 h-4 w-4 animate-spin" />
												Saving...
											</>
										) : (
											"Crop & Save"
										)}
									</Button>
									<Button
										variant="outline"
										onClick={() => {
											reset();
										}}
										disabled={uploading}
									>
										Cancel
									</Button>
								</div>
							</div>
						)}
					</div>
				</DialogContent>
			</Dialog>
		</>
	);
}
